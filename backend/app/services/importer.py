"""Import a public stream URL (YouTube, SoundCloud, ...) into the library."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncConnection

from ..config import get_settings
from ..db import execute, fetch_one, fetch_val, get_engine
from . import catalog, resolver
from .covers import store_cover
from .lyrics import fetch_lrclib

log = logging.getLogger("sonicvault.importer")


async def _max_video_height(conn: AsyncConnection) -> int:
    return await fetch_val(conn, "SELECT video_max_height FROM user_preferences WHERE id = 1") or 1080


async def upsert_remote_source(conn: AsyncConnection, *, track_id: int, kind: str, extractor: str,
                               remote_id: str, page_url: str, src: dict, duration_ms: int | None) -> int:
    return await fetch_val(conn, """
        INSERT INTO media_sources (track_id, kind, origin, extractor, remote_id, page_url, format_id,
                                   cached_stream_url, stream_expires_at, stream_headers, mime_type,
                                   container, audio_codec, video_codec, bitrate_kbps, sample_rate_hz,
                                   channels, width, height, fps, file_size_bytes, duration_ms)
        VALUES (:track, CAST(:kind AS media_kind), 'remote', :ex, :rid, :page, :format_id,
                :cached_stream_url, :stream_expires_at, CAST(:headers AS jsonb), :mime_type,
                :container, :audio_codec, :video_codec, :bitrate_kbps, :sample_rate_hz,
                :channels, :width, :height, :fps, :file_size_bytes, :dur)
        ON CONFLICT (extractor, remote_id, kind) WHERE origin = 'remote' DO UPDATE SET
            format_id = EXCLUDED.format_id, cached_stream_url = EXCLUDED.cached_stream_url,
            stream_expires_at = EXCLUDED.stream_expires_at, stream_headers = EXCLUDED.stream_headers,
            mime_type = EXCLUDED.mime_type, container = EXCLUDED.container,
            audio_codec = EXCLUDED.audio_codec, video_codec = EXCLUDED.video_codec,
            bitrate_kbps = EXCLUDED.bitrate_kbps, sample_rate_hz = EXCLUDED.sample_rate_hz,
            channels = EXCLUDED.channels, width = EXCLUDED.width, height = EXCLUDED.height,
            fps = EXCLUDED.fps, file_size_bytes = EXCLUDED.file_size_bytes, is_available = true
        RETURNING id
    """, track=track_id, kind=kind, ex=extractor, rid=remote_id, page=page_url,
        headers=json.dumps(src.pop("stream_headers")), dur=duration_ms, **src)


async def import_url(
    client: httpx.AsyncClient,
    url: str,
    *,
    kinds: tuple[str, ...] = ("audio", "video"),
    track_id: int | None = None,
    fetch_lyrics: bool = True,
    in_library: bool = True,
    meta_override: dict | None = None,
) -> dict:
    """Resolve `url` with yt-dlp and add it to the library.

    * Re-importing the same video is idempotent (returns the existing track).
    * `track_id` attaches the renditions to an existing track, e.g. adding the
      official music video to a song from your local FLAC collection.
    * Otherwise the track is paired automatically by title + artist.
    * in_library=False stores a catalogue play without adding it to Your
      Library (it joins the library when liked or added to a playlist).
    * meta_override (title, artist, featured, album, thumbnail, ...) replaces
      metadata guessed from the video title, e.g. clean YouTube Music data.
    """
    info = await resolver.extract(url)
    extractor = (info.get("extractor_key") or info.get("extractor") or "generic").lower()
    remote_id = str(info.get("id") or "")
    if not remote_id:
        raise resolver.ResolveError("extractor returned no media id")
    page_url = resolver.page_url_for(info)
    meta = resolver.clean_metadata(info)
    featured: list[str] = []
    if meta_override:
        featured = [f for f in meta_override.pop("featured", None) or [] if f]
        meta.update({k: v for k, v in meta_override.items() if v})
    fmts = info.get("formats") or [info]

    async with get_engine().begin() as conn:
        existing = await fetch_val(conn, """
            SELECT track_id FROM media_sources
             WHERE origin = 'remote' AND extractor = :ex AND remote_id = :rid
             ORDER BY id LIMIT 1
        """, ex=extractor, rid=remote_id)
        max_h = await _max_video_height(conn)

    picked: dict[str, dict] = {}
    if "audio" in kinds and (f := resolver.pick_audio(fmts)):
        picked["audio"] = resolver.format_to_source(f, "audio")
    if "video" in kinds and (f := resolver.pick_video(fmts, max_h)):
        picked["video"] = resolver.format_to_source(f, "video")
    if not picked:
        raise resolver.ResolveError("no playable audio or video formats found")

    cover = None
    if meta["thumbnail"]:
        data = await resolver.fetch_bytes(client, meta["thumbnail"])
        if data:
            cover = await asyncio.to_thread(store_cover, data, crop_square=True)

    async with get_engine().begin() as conn:
        artist_id = await catalog.get_or_create_artist(conn, meta["artist"]) if meta["artist"] else None
        album_id = await catalog.get_or_create_album(
            conn, title=meta["album"] or meta["title"], album_artist_id=artist_id,
            album_type="album" if meta["album"] else "single",
            release_year=meta["release_year"], genre=meta["genre"],
        )
        if cover:
            await catalog.set_album_cover(conn, album_id, cover)

        created = False
        if track_id is None:
            track_id = existing
        if track_id is None:
            first_kind = "video" if "video" in picked else "audio"
            track_id = await catalog.find_pairable_track(
                conn, title=meta["title"], artist_id=artist_id, kind=first_kind,
                album_id=album_id, exclude_origin="remote",
            )
        if track_id is None:
            track_id = await catalog.create_track(
                conn, title=meta["title"], album_id=album_id, artist_id=artist_id,
                duration_ms=meta["duration_ms"], genre=meta["genre"], release_year=meta["release_year"],
            )
            featured_ids = [await catalog.get_or_create_artist(conn, n) for n in featured]
            await catalog.set_track_artists(conn, track_id, artist_id, featured_ids)
            await execute(conn, "UPDATE tracks SET in_library = :lib WHERE id = :id", id=track_id, lib=in_library)
            created = True
        else:
            if not await fetch_val(conn, "SELECT 1 FROM tracks WHERE id = :id", id=track_id):
                raise resolver.ResolveError(f"track {track_id} does not exist")
            await catalog.attach_album_if_missing(conn, track_id, album_id, meta["duration_ms"])
            if in_library:   # an explicit add promotes an earlier catalogue play
                await execute(conn, "UPDATE tracks SET in_library = true WHERE id = :id AND NOT in_library", id=track_id)

        source_ids = {}
        for kind, src in picked.items():
            source_ids[kind] = await upsert_remote_source(
                conn, track_id=track_id, kind=kind, extractor=extractor, remote_id=remote_id,
                page_url=page_url, src=dict(src), duration_ms=meta["duration_ms"],
            )
        await catalog.refresh_preferred(conn, track_id)

        has_lyrics = await fetch_val(conn, "SELECT 1 FROM lyrics WHERE track_id = :t", t=track_id)

    lyrics_found = False
    if fetch_lyrics and not has_lyrics and meta["artist"]:
        found = await fetch_lrclib(client, title=meta["title"], artist=meta["artist"],
                                   album=meta["album"], duration_ms=meta["duration_ms"])
        if found:
            async with get_engine().begin() as conn:
                await catalog.upsert_lyrics(conn, track_id, synced=found["synced_lines"],
                                            plain=found["plain_text"], source="remote")
            lyrics_found = True

    async with get_engine().begin() as conn:
        track = await catalog.get_track_brief(conn, track_id)
    return {
        "track": track,
        "created": created,
        "source_ids": source_ids,
        "extractor": extractor,
        "remote_id": remote_id,
        "lyrics_found": lyrics_found,
    }


async def refresh_source(conn: AsyncConnection, src: dict) -> dict:
    """Re-resolve an expired remote source and persist the fresh URL."""
    max_h = await _max_video_height(conn)
    fresh = await resolver.resolve_format(src["page_url"], src["kind"], max_h)
    headers = fresh.pop("stream_headers")
    await execute(conn, """
        UPDATE media_sources SET
            format_id = :format_id, cached_stream_url = :cached_stream_url,
            stream_expires_at = :stream_expires_at, stream_headers = CAST(:headers AS jsonb),
            mime_type = :mime_type, container = :container, audio_codec = :audio_codec,
            video_codec = :video_codec, bitrate_kbps = :bitrate_kbps, sample_rate_hz = :sample_rate_hz,
            channels = :channels, width = :width, height = :height, fps = :fps,
            file_size_bytes = :file_size_bytes, is_available = true
         WHERE id = :id
    """, id=src["id"], headers=json.dumps(headers), **fresh)
    return {**src, **fresh, "stream_headers": headers}


# --- server-side downloads (keep a local copy; survives link rot) --------------------------

@dataclass
class DownloadJob:
    source_id: int
    status: str = "queued"          # queued | downloading | done | error
    error: str | None = None
    local_source_id: int | None = None
    file_path: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class Downloader:
    def __init__(self) -> None:
        self.jobs: dict[int, DownloadJob] = {}
        self._sem = asyncio.Semaphore(2)
        self._tasks: set[asyncio.Task] = set()

    def status(self, source_id: int | None = None) -> list[dict]:
        jobs = [self.jobs[source_id]] if source_id in self.jobs else ([] if source_id else self.jobs.values())
        return [asdict(j) for j in jobs]

    def start(self, source_id: int) -> DownloadJob:
        job = self.jobs.get(source_id)
        if job and job.status in ("queued", "downloading"):
            return job
        job = self.jobs[source_id] = DownloadJob(source_id)
        task = asyncio.create_task(self._run(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _run(self, job: DownloadJob) -> None:
        async with self._sem:
            job.status = "downloading"
            try:
                async with get_engine().begin() as conn:
                    src = await fetch_one(conn, "SELECT * FROM media_sources WHERE id = :id AND origin = 'remote'",
                                          id=job.source_id)
                    if not src:
                        raise resolver.ResolveError("remote source not found")
                    src = await refresh_source(conn, src)
                dest = get_settings().download_dir / src["extractor"]
                stem = f"{src['remote_id']}.{src['kind']}"
                path: Path = await resolver.download(src["page_url"], src["format_id"], dest, stem)
                st = path.stat()
                async with get_engine().begin() as conn:
                    job.local_source_id = await fetch_val(conn, """
                        INSERT INTO media_sources (track_id, kind, origin, file_path, file_size_bytes, file_mtime,
                                                   mime_type, container, audio_codec, video_codec, bitrate_kbps,
                                                   sample_rate_hz, channels, width, height, fps, duration_ms,
                                                   sync_offset_ms)
                        VALUES (:track, CAST(:kind AS media_kind), 'local', :fp, :size, :mtime,
                                :mime, :container, :ac, :vc, :br, :sr, :ch, :w, :h, :fps, :dur, :sync)
                        ON CONFLICT (file_path, kind) WHERE origin = 'local' DO UPDATE SET
                            file_size_bytes = EXCLUDED.file_size_bytes, file_mtime = EXCLUDED.file_mtime,
                            is_available = true
                        RETURNING id
                    """, track=src["track_id"], kind=src["kind"], fp=str(path.resolve()), size=st.st_size,
                        mtime=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc), mime=src["mime_type"],
                        container=path.suffix.lstrip(".").lower(), ac=src["audio_codec"], vc=src["video_codec"],
                        br=src["bitrate_kbps"], sr=src["sample_rate_hz"], ch=src["channels"],
                        w=src["width"], h=src["height"], fps=src["fps"], dur=src["duration_ms"],
                        sync=src["sync_offset_ms"])
                    await catalog.refresh_preferred(conn, src["track_id"])
                job.file_path = str(path)
                job.status = "done"
            except Exception as e:
                log.exception("download of source %s failed", job.source_id)
                job.status, job.error = "error", str(e)[:500]


downloader = Downloader()
