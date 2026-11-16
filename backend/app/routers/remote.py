"""yt-dlp powered search / import / refresh / download."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import fetch_all, fetch_one, get_engine
from ..deps import get_http, not_found
from ..schemas import RemoteImport
from ..services import importer, resolver

router = APIRouter(prefix="/api", tags=["remote"])


@router.get("/remote/search")
async def remote_search(q: str = Query(min_length=1, max_length=200),
                        limit: int = Query(default=15, ge=1, le=50)) -> dict:
    """Search YouTube. Each result can be passed to /api/remote/import."""
    try:
        results = await resolver.search(q, limit)
    except Exception as e:  # yt-dlp raises many types; surface them as a gateway error
        raise HTTPException(status_code=502, detail=f"search failed: {e}") from e
    owned: dict[str, int] = {}
    if results:
        async with get_engine().begin() as conn:
            rows = await fetch_all(conn, """
                SELECT DISTINCT remote_id, track_id FROM media_sources
                 WHERE origin = 'remote' AND remote_id = ANY(CAST(:ids AS text[]))
            """, ids=[r["id"] for r in results])
        owned = {r["remote_id"]: r["track_id"] for r in rows}
    for r in results:
        r["in_library_track_id"] = owned.get(r["id"])
    return {"query": q, "results": results}


@router.post("/remote/import", status_code=201)
async def remote_import(body: RemoteImport, http: httpx.AsyncClient = Depends(get_http)) -> dict:
    """Resolve a URL with yt-dlp and add it to the library as audio and/or
    video renditions. Pass `track_id` to attach a music video to an existing song."""
    try:
        return await importer.import_url(http, body.url, kinds=tuple(body.kinds),
                                         track_id=body.track_id, fetch_lyrics=body.fetch_lyrics)
    except resolver.ResolveError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/sources/{source_id}/refresh")
async def refresh(source_id: int) -> dict:
    """Force a fresh stream URL (normally automatic on expiry)."""
    async with get_engine().begin() as conn:
        src = await fetch_one(conn, "SELECT * FROM media_sources WHERE id = :id", id=source_id)
        if not src:
            raise not_found("media source")
        if src["origin"] != "remote":
            raise HTTPException(status_code=400, detail="only remote sources can be refreshed")
        try:
            fresh = await importer.refresh_source(conn, src)
        except resolver.ResolveError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
    return {k: fresh[k] for k in ("id", "kind", "format_id", "stream_expires_at", "mime_type",
                                  "width", "height", "bitrate_kbps")}


@router.post("/sources/{source_id}/download", status_code=202)
async def download(source_id: int) -> dict:
    """Save a remote rendition to DOWNLOAD_DIR and register it as a local
    source, so it keeps playing even if the upload disappears."""
    async with get_engine().begin() as conn:
        src = await fetch_one(conn, "SELECT origin FROM media_sources WHERE id = :id", id=source_id)
    if not src:
        raise not_found("media source")
    if src["origin"] != "remote":
        raise HTTPException(status_code=400, detail="source is already local")
    job = importer.downloader.start(source_id)
    return importer.downloader.status(job.source_id)[0]


@router.get("/downloads")
async def downloads(source_id: int | None = None) -> list[dict]:
    return importer.downloader.status(source_id)
