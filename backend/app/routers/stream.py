"""Playback endpoints: byte-range streaming, playback manifests, cover art."""
from __future__ import annotations

import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from ..db import execute, fetch_all, fetch_one, get_engine
from ..deps import get_http, not_found
from ..services import importer, resolver
from ..services.covers import cover_root, thumb_rel
from ..services.proxy import UpstreamError, UpstreamExpired, proxy_stream
from ..services.ranges import range_file_response

log = logging.getLogger("sonicvault.stream")
router = APIRouter(tags=["playback"])

_SOURCE_PUBLIC_COLS = """
    id, track_id, kind, origin, extractor, remote_id, page_url, mime_type, container,
    audio_codec, video_codec, bitrate_kbps, sample_rate_hz, bit_depth, channels,
    width, height, fps, duration_ms, sync_offset_ms, is_preferred, is_available,
    file_size_bytes
"""


def stream_url(source_id: int) -> str:
    return f"/api/stream/{source_id}"


async def _load_source(source_id: int) -> dict:
    async with get_engine().begin() as conn:
        src = await fetch_one(conn, "SELECT * FROM media_sources WHERE id = :id", id=source_id)
    if not src or not src["is_available"]:
        raise not_found("media source")
    return src


async def _fresh_remote(src: dict, *, force: bool = False) -> dict:
    """Return the source with a valid CDN URL, re-resolving with yt-dlp if needed.
    A per-source lock collapses a burst of parallel range requests into one resolve."""
    if not force and not resolver.needs_refresh(src):
        return src
    async with resolver.refresh_lock(src["id"]):
        current = await _load_source(src["id"])
        # Someone else refreshed while we waited for the lock?
        if not resolver.needs_refresh(current) and (
            not force or current["cached_stream_url"] != src["cached_stream_url"]
        ):
            return current
        async with get_engine().begin() as conn:
            try:
                return await importer.refresh_source(conn, current)
            except resolver.ResolveError as e:
                raise HTTPException(status_code=502, detail=f"could not resolve stream: {e}") from e


@router.get("/api/stream/{source_id}")
@router.head("/api/stream/{source_id}", include_in_schema=False)
async def stream(source_id: int, request: Request, http: httpx.AsyncClient = Depends(get_http)) -> Response:
    """Stream a media source. Supports `Range` (206 Partial Content) for
    instant seeking, for both local files and remote (proxied) streams."""
    src = await _load_source(source_id)

    if src["origin"] == "local":
        path = Path(src["file_path"])
        if not path.is_file():
            async with get_engine().begin() as conn:
                await execute(conn, "UPDATE media_sources SET is_available = false WHERE id = :id", id=source_id)
            raise not_found("media file")
        # no-store: browsers' HTTP caches mishandle partial (206) media entries;
        # offline copies are managed explicitly by the service worker (Phase 5).
        return range_file_response(request, path, src["mime_type"], cache_control="no-store")

    src = await _fresh_remote(src)
    for attempt in range(2):
        try:
            return await proxy_stream(
                request, http, src["cached_stream_url"], src["stream_headers"],
                media_type=src["mime_type"] or "application/octet-stream",
                known_size=src["file_size_bytes"],
            )
        except UpstreamExpired:
            if attempt == 1:
                raise HTTPException(status_code=502, detail="upstream keeps rejecting the stream URL")
            log.info("stream URL for source %s rejected upstream; re-resolving", source_id)
            src = await _fresh_remote(src, force=True)
        except UpstreamError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        except httpx.HTTPError as e:
            raise HTTPException(status_code=504, detail=f"upstream unreachable: {e}") from e
    raise HTTPException(status_code=502)


@router.get("/api/tracks/{track_id}/playback")
async def playback_manifest(
    track_id: int,
    audio_source_id: int | None = Query(default=None, description="override the preferred audio source"),
    video_source_id: int | None = Query(default=None, description="override the preferred video source"),
) -> dict:
    """Everything the player needs to start a track: audio + (optional) video
    rendition with stream URLs, the A/V sync offset, and synced lyrics."""
    async with get_engine().begin() as conn:
        track = await fetch_one(conn, "SELECT * FROM v_tracks WHERE id = :id", id=track_id)
        if not track:
            raise not_found("track")
        sources = await fetch_all(conn, f"""
            SELECT {_SOURCE_PUBLIC_COLS} FROM media_sources
             WHERE track_id = :id AND is_available
             ORDER BY kind, is_preferred DESC, id
        """, id=track_id)
        artists = await fetch_all(conn, """
            SELECT a.id, a.name, ta.role FROM track_artists ta JOIN artists a ON a.id = ta.artist_id
             WHERE ta.track_id = :id ORDER BY ta.position
        """, id=track_id)
        lyrics = await fetch_one(conn, """
            SELECT synced_lines, plain_text, offset_ms, source FROM lyrics WHERE track_id = :id
        """, id=track_id)
        gain = await fetch_one(conn, """
            SELECT replaygain_track_db::float AS replaygain_track_db,
                   replaygain_album_db::float AS replaygain_album_db
              FROM tracks WHERE id = :id
        """, id=track_id)
        track.update(gain or {})

    def choose(kind: str, override: int | None) -> dict | None:
        cands = [s for s in sources if s["kind"] == kind]
        if override is not None:
            match = next((s for s in cands if s["id"] == override), None)
            if not match:
                raise HTTPException(status_code=400, detail=f"{kind} source {override} not on this track")
            return match
        return next((s for s in cands if s["is_preferred"]), cands[0] if cands else None)

    audio, video = choose("audio", audio_source_id), choose("video", video_source_id)
    for s in (audio, video):
        if s:
            s["url"] = stream_url(s["id"])
    cover = track.get("cover_path")
    return {
        "track": {
            **track,
            "cover_url": f"/api/covers/{cover}" if cover else None,
            "cover_thumb_url": f"/api/covers/{cover}?size=300" if cover else None,
            "artists": artists,
        },
        "audio": audio,
        "video": video,
        "alternatives": [{**s, "url": stream_url(s["id"])} for s in sources
                         if s not in (audio, video)],
        "lyrics": lyrics,
    }


@router.get("/api/covers/{cover_path:path}")
async def cover(cover_path: str, request: Request, size: int | None = Query(default=None)) -> Response:
    """Cover art. Paths are content hashes, so responses are cached forever."""
    root = cover_root().resolve()
    rel = thumb_rel(cover_path) if size and size <= 300 else cover_path
    path = (root / rel).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise not_found("cover")
    return range_file_response(request, path, "image/jpeg",
                               cache_control="public, max-age=31536000, immutable")
