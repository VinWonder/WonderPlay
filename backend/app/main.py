"""WonderPlay API.

Run from the backend folder (with the venv active):
    uvicorn app.main:app --reload
Then open http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import asyncio
import logging
import mimetypes
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import dispose_engine, fetch_val, get_engine
from . import auth
from .migrate import migrate
from .routers import catalog, library, me, playlists, remote, stream
from .services import resolver
from .services.scanner import ensure_env_folders, scanner

mimetypes.add_type("application/manifest+json", ".webmanifest")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("sonicvault")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    s.cover_cache_dir.mkdir(parents=True, exist_ok=True)
    s.download_dir.mkdir(parents=True, exist_ok=True)

    app.state.http = httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(20.0, read=60.0),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
    )

    if s.auto_migrate:
        await migrate()
    async with get_engine().begin() as conn:  # fail fast on a bad DATABASE_URL
        await fetch_val(conn, "SELECT 1")
    await ensure_env_folders(s.media_root_list)
    app.state.warmup = asyncio.create_task(resolver.warm_up())
    if s.scan_on_startup:
        scanner.start()  # incremental: unchanged files are skipped, so this is cheap
    log.info("WonderPlay ready on http://%s:%s  (API docs at /docs)", s.host, s.port)

    yield

    await app.state.http.aclose()
    await dispose_engine()


app = FastAPI(
    title="WonderPlay",
    version="0.2.0",
    description="Personal ad-free music & music-video streaming server.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
    # Let the player read range metadata from cross-origin dev servers.
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "ETag"],
)

app.add_middleware(auth.AuthMiddleware)
app.include_router(auth.router)
app.include_router(stream.router)
app.include_router(library.router)
app.include_router(remote.router)
app.include_router(playlists.router)
app.include_router(me.router)
app.include_router(catalog.router)


@app.get("/api/health", tags=["meta"])
async def health() -> dict:
    async with get_engine().begin() as conn:
        db_ok = await fetch_val(conn, "SELECT 1") == 1
    return {"status": "ok", "db": db_ok, "scanning": scanner.running}


# The PWA (Phase 3+) is served from the same origin so the service worker,
# Media Session and Web Audio work without CORS. Mounted last: /api wins.
_frontend = get_settings().frontend_dir
if _frontend.is_dir():
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="frontend")
