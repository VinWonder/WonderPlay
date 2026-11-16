"""Local library scanner.

Walks enabled `library_folders`, reads tags with mutagen (in a worker thread),
and upserts artists / albums / tracks / media_sources / lyrics. Re-scans are
incremental: files whose size + mtime are unchanged are skipped, and files
that vanished are flagged `is_available = false` (play history is kept).
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..db import execute, fetch_all, get_engine
from . import catalog
from .covers import store_cover
from .ranges import guess_mime
from .tags import MEDIA_EXTS, VIDEO_EXTS, TagInfo, find_folder_art, read_tags

log = logging.getLogger("sonicvault.scanner")


@dataclass
class ScanProgress:
    running: bool = False
    folder_id: int | None = None
    folder_path: str | None = None
    total: int = 0
    processed: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    missing: int = 0
    errors: int = 0
    current_file: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_samples: list[str] = field(default_factory=list)


def _norm(p: Path | str) -> str:
    """Canonical absolute path string used as the DB key."""
    return os.path.normcase(os.path.abspath(str(p)))


def _walk(root: Path, recursive: bool) -> list[tuple[Path, os.stat_result]]:
    found: list[tuple[Path, os.stat_result]] = []
    if recursive:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                p = Path(dirpath) / name
                if p.suffix.lower() in MEDIA_EXTS:
                    try:
                        found.append((p, p.stat()))
                    except OSError:
                        pass
    else:
        for p in root.iterdir():
            if p.is_file() and p.suffix.lower() in MEDIA_EXTS:
                try:
                    found.append((p, p.stat()))
                except OSError:
                    pass
    return found


class Scanner:
    def __init__(self) -> None:
        self.progress = ScanProgress()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict:
        return asdict(self.progress)

    def start(self, folder_ids: list[int] | None = None, *, full: bool = False) -> bool:
        """Kick off a background scan. Returns False if one is already running."""
        if self.running:
            return False
        self._task = asyncio.create_task(self._run(folder_ids, full))
        return True

    async def wait(self) -> None:
        if self._task:
            await self._task

    async def _run(self, folder_ids: list[int] | None, full: bool) -> None:
        async with self._lock:
            async with get_engine().begin() as conn:
                folders = await fetch_all(conn, """
                    SELECT id, path, recursive FROM library_folders
                     WHERE is_enabled AND (CAST(:ids AS bigint[]) IS NULL OR id = ANY(CAST(:ids AS bigint[])))
                     ORDER BY id
                """, ids=folder_ids)
            self.progress = ScanProgress(running=True, started_at=datetime.now(timezone.utc))
            try:
                for f in folders:
                    await self._scan_folder(f["id"], Path(f["path"]), f["recursive"], full)
            finally:
                self.progress.running = False
                self.progress.current_file = None
                self.progress.finished_at = datetime.now(timezone.utc)

    async def _scan_folder(self, folder_id: int, root: Path, recursive: bool, full: bool) -> None:
        p = self.progress
        p.folder_id, p.folder_path = folder_id, str(root)
        engine = get_engine()
        async with engine.begin() as conn:
            await execute(conn, """UPDATE library_folders
                                      SET status = 'scanning', last_scan_started_at = now(), last_error = NULL
                                    WHERE id = :id""", id=folder_id)
        try:
            if not root.is_dir():
                raise FileNotFoundError(f"folder not found: {root}")
            files = await asyncio.to_thread(_walk, root, recursive)
            p.total += len(files)

            prefix = _norm(root).rstrip("\\/") + os.sep
            async with engine.begin() as conn:
                rows = await fetch_all(conn, """
                    SELECT id, track_id, file_path, file_size_bytes, file_mtime, kind, is_available
                      FROM media_sources
                     WHERE origin = 'local' AND left(file_path, length(:prefix)) = :prefix
                """, prefix=prefix)
            existing: dict[str, list[dict]] = {}
            for r in rows:
                existing.setdefault(r["file_path"], []).append(r)

            seen: set[str] = set()
            art_cache: dict[str, bytes | None] = {}
            for path, st in files:
                key = _norm(path)
                seen.add(key)
                p.current_file = str(path)
                try:
                    await self._process_file(path, key, st, existing.get(key, []), art_cache, full)
                except Exception as e:  # one bad file must never stop the scan
                    p.errors += 1
                    if len(p.error_samples) < 20:
                        p.error_samples.append(f"{path}: {e}")
                    log.exception("scan failed for %s", path)
                p.processed += 1

            gone = [r["id"] for k, rs in existing.items() if k not in seen for r in rs if r["is_available"]]
            async with engine.begin() as conn:
                if gone:
                    await execute(conn, "UPDATE media_sources SET is_available = false WHERE id = ANY(:ids)",
                                  ids=gone)
                    tids = {r["track_id"] for k, rs in existing.items() if k not in seen for r in rs}
                    for tid in tids:
                        await catalog.refresh_preferred(conn, tid)
                p.missing += len(gone)
                await execute(conn, """UPDATE library_folders
                                          SET status = 'idle', last_scan_finished_at = now(),
                                              last_scan_file_count = :n
                                        WHERE id = :id""", id=folder_id, n=len(files))
        except Exception as e:
            if isinstance(e, FileNotFoundError):
                log.warning("skipping library folder %s: it does not exist", root)
            else:
                log.exception("scan of folder %s failed", root)
            async with engine.begin() as conn:
                await execute(conn, """UPDATE library_folders
                                          SET status = 'error', last_scan_finished_at = now(), last_error = :err
                                        WHERE id = :id""", id=folder_id, err=str(e)[:2000])

    async def _process_file(self, path: Path, key: str, st: os.stat_result, rows: list[dict],
                            art_cache: dict[str, bytes | None], full: bool) -> None:
        p = self.progress
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        unchanged = rows and all(
            r["file_size_bytes"] == st.st_size and r["file_mtime"] is not None
            and abs((r["file_mtime"] - mtime).total_seconds()) < 1
            for r in rows
        )
        if unchanged and not full:
            if not all(r["is_available"] for r in rows):
                async with get_engine().begin() as conn:
                    await execute(conn, "UPDATE media_sources SET is_available = true WHERE id = ANY(:ids)",
                                  ids=[r["id"] for r in rows])
                    await catalog.refresh_preferred(conn, rows[0]["track_id"])
            p.unchanged += 1
            return

        info: TagInfo = await asyncio.to_thread(read_tags, path)
        is_video_file = path.suffix.lower() in VIDEO_EXTS

        cover_bytes = info.cover
        if cover_bytes is None:
            d = str(path.parent)
            if d not in art_cache:
                art = find_folder_art(path.parent)
                art_cache[d] = await asyncio.to_thread(art.read_bytes) if art else None
            cover_bytes = art_cache[d]
        cover = await asyncio.to_thread(store_cover, cover_bytes) if cover_bytes else None

        async with get_engine().begin() as conn:
            artist_id = await catalog.get_or_create_artist(conn, info.artist) if info.artist else None
            featured_ids = [await catalog.get_or_create_artist(conn, n) for n in info.featured]

            album_artist_name = info.album_artist or ("Various Artists" if info.compilation else info.artist)
            album_artist_id = (await catalog.get_or_create_artist(conn, album_artist_name)
                               if album_artist_name else None)
            album_id = await catalog.get_or_create_album(
                conn,
                title=info.album or info.title,
                album_artist_id=album_artist_id,
                album_type="compilation" if info.compilation else ("album" if info.album else "single"),
                release_year=info.year, release_date=info.date, genre=info.genre,
                total_tracks=info.total_tracks, total_discs=info.total_discs,
            )
            if cover:
                await catalog.set_album_cover(conn, album_id, cover)

            track_fields = dict(
                title=info.title, album_id=album_id, artist_id=artist_id,
                disc_number=info.disc_number, track_number=info.track_number,
                duration_ms=info.duration_ms, genre=info.genre, release_year=info.year,
                isrc=info.isrc, bpm=info.bpm, explicit=info.explicit,
                replaygain_track_db=info.replaygain_track_db, replaygain_album_db=info.replaygain_album_db,
            )

            if rows:
                track_id = rows[0]["track_id"]
                # Audio files are the metadata authority; a paired music-video
                # file must not overwrite the album track's tags.
                if not is_video_file:
                    await catalog.update_track(conn, track_id, **track_fields)
                    await catalog.set_track_artists(conn, track_id, artist_id, featured_ids)
                p.updated += 1
            else:
                track_id = await catalog.find_pairable_track(
                    conn, title=info.title, artist_id=artist_id,
                    kind="video" if is_video_file else "audio",
                    album_id=album_id, exclude_origin="local",
                )
                if track_id is None:
                    track_id = await catalog.create_track(conn, **track_fields)
                    await catalog.set_track_artists(conn, track_id, artist_id, featured_ids)
                elif not is_video_file:
                    await catalog.update_track(conn, track_id, **track_fields)
                    await catalog.set_track_artists(conn, track_id, artist_id, featured_ids)
                else:
                    await catalog.attach_album_if_missing(conn, track_id, album_id, info.duration_ms)
                p.added += 1

            # A music-video file yields two renditions: the picture (video) and
            # its soundtrack (audio) so Song Mode works even without a separate file.
            kinds = ("video", "audio") if is_video_file else ("audio",)
            for kind in kinds:
                await self._upsert_source(conn, track_id, key, kind, st, mtime, info, path)

            if info.lyrics_synced or info.lyrics_plain:
                await catalog.upsert_lyrics(
                    conn, track_id, synced=info.lyrics_synced, plain=info.lyrics_plain,
                    source="lrc_file" if path.with_suffix(".lrc").exists() else "embedded",
                )
            await catalog.refresh_preferred(conn, track_id)

    @staticmethod
    async def _upsert_source(conn, track_id: int, key: str, kind: str, st: os.stat_result,
                             mtime: datetime, info: TagInfo, path: Path) -> None:
        mime = guess_mime(path)
        if kind == "audio" and mime.startswith("video/"):
            mime = mime.replace("video/", "audio/")  # <audio> happily plays an mp4's AAC track
        await execute(conn, """
            INSERT INTO media_sources (track_id, kind, origin, file_path, file_size_bytes, file_mtime,
                                       mime_type, container, audio_codec, bitrate_kbps, sample_rate_hz,
                                       bit_depth, channels, duration_ms, is_available)
            VALUES (:track, CAST(:kind AS media_kind), 'local', :fp, :size, :mtime,
                    :mime, :container, :codec, :br, :sr, :bd, :ch, :dur, true)
            ON CONFLICT (file_path, kind) WHERE origin = 'local' DO UPDATE SET
                track_id = EXCLUDED.track_id, file_size_bytes = EXCLUDED.file_size_bytes,
                file_mtime = EXCLUDED.file_mtime, mime_type = EXCLUDED.mime_type,
                container = EXCLUDED.container, audio_codec = EXCLUDED.audio_codec,
                bitrate_kbps = EXCLUDED.bitrate_kbps, sample_rate_hz = EXCLUDED.sample_rate_hz,
                bit_depth = EXCLUDED.bit_depth, channels = EXCLUDED.channels,
                duration_ms = EXCLUDED.duration_ms, is_available = true
        """, track=track_id, kind=kind, fp=key, size=st.st_size, mtime=mtime, mime=mime,
            container=info.container, codec=info.audio_codec, br=info.bitrate_kbps,
            sr=info.sample_rate_hz, bd=info.bit_depth, ch=info.channels, dur=info.duration_ms)


async def ensure_env_folders(roots: list[str]) -> None:
    """Register MEDIA_ROOTS from .env as library folders (idempotent)."""
    if not roots:
        return
    async with get_engine().begin() as conn:
        for r in roots:
            await execute(conn, "INSERT INTO library_folders (path) VALUES (:p) ON CONFLICT (path) DO NOTHING",
                          p=_norm(Path(r)))


scanner = Scanner()
