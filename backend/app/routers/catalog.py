"""Spotify-style discovery over the YouTube Music catalogue.

Catalogue items are playable straight away: the first play "resolves" a
video id into a stored (but not-in-library) track via yt-dlp, then it streams
like anything else. Liking it or adding it to a playlist saves it.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from ..config import get_settings
from ..db import execute, fetch_all, fetch_one, fetch_val, get_engine
from ..deps import get_http
from ..services import catalog as catalog_db
from ..services import importer, resolver, ytmusic
from ..services.lyrics import fetch_lrclib

log = logging.getLogger("wonderplay.catalog")
router = APIRouter(prefix="/api/catalog", tags=["catalog"])

_VID_RE = re.compile(r"^[\w-]{11}$")


def _upstream_error(err: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail=f"YouTube Music is unavailable right now ({type(err).__name__})")


# --- annotate catalogue tracks with library state ----------------------------------------
async def annotate(tracks: list[dict]) -> list[dict]:
    """Attach library ids / likes / local art to catalogue tracks we already have."""
    vids = list({t["video_id"] for t in tracks if t.get("video_id")})
    if not vids:
        return tracks
    async with get_engine().begin() as conn:
        rows = await fetch_all(conn, """
            SELECT DISTINCT ON (ms.remote_id) ms.remote_id AS vid, v.*
              FROM media_sources ms JOIN v_tracks v ON v.id = ms.track_id
             WHERE ms.origin = 'remote' AND ms.extractor = 'youtube' AND ms.remote_id = ANY(CAST(:v AS text[]))
             ORDER BY ms.remote_id, ms.id
        """, v=vids)
    by_vid = {r["vid"]: r for r in rows}
    for t in tracks:
        r = by_vid.get(t.get("video_id"))
        if not r:
            continue
        for k in ("id", "is_favorite", "in_library", "cover_path", "dominant_color", "album_id", "artist_id",
                  "has_synced_lyrics", "play_count"):
            t[k] = r[k]
        t["has_video"] = bool(t.get("has_video") or r["has_video"])
    return tracks


async def _country() -> str:
    async with get_engine().begin() as conn:
        c = await fetch_val(conn, "SELECT extra->>'country' FROM user_preferences WHERE id = 1")
    return c if c and re.fullmatch(r"[A-Z]{2}", c) else "ZZ"


# --- browse ---------------------------------------------------------------------------------
@router.get("/search")
async def search(q: str = Query(min_length=1, max_length=200)) -> dict:
    try:
        r = await ytmusic.search(q)
    except Exception as err:  # noqa: BLE001
        raise _upstream_error(err) from err
    await annotate(r["songs"] + r["videos"] + ([r["top"]] if r["top"] and r["top"]["type"] == "track" else []))
    return r


@router.get("/artist/{browse_id}")
async def artist(browse_id: str, name: str | None = Query(default=None, max_length=200)) -> dict:
    try:
        a = await ytmusic.artist(browse_id, name)
    except LookupError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except Exception as err:  # noqa: BLE001
        raise _upstream_error(err) from err
    await annotate(a["songs"] + a["videos"])
    return a


@router.get("/album/{browse_id}")
async def album(browse_id: str) -> dict:
    try:
        a = await ytmusic.album(browse_id)
    except Exception as err:  # noqa: BLE001
        raise _upstream_error(err) from err
    await annotate(a["tracks"])
    return a


@router.get("/playlist/{playlist_id}")
async def playlist(playlist_id: str) -> dict:
    try:
        p = await ytmusic.playlist(playlist_id)
    except Exception as err:  # noqa: BLE001
        raise _upstream_error(err) from err
    await annotate(p["tracks"])
    return p


@router.get("/moods")
async def moods() -> list[dict]:
    try:
        return await ytmusic.moods()
    except Exception as err:  # noqa: BLE001
        raise _upstream_error(err) from err


@router.get("/mood")
async def mood(params: str = Query(min_length=4, max_length=400)) -> list[dict]:
    try:
        return await ytmusic.mood_playlists(params)
    except Exception as err:  # noqa: BLE001
        raise _upstream_error(err) from err


@router.get("/radio")
async def radio(video_id: str | None = None, track_id: int | None = None) -> dict:
    """'Song radio': an endless stream of similar songs (also powers Autoplay)."""
    seed = video_id
    if not seed and track_id:
        async with get_engine().begin() as conn:
            t = await fetch_one(conn, "SELECT title, artist_name, youtube_id FROM v_tracks WHERE id = :id", id=track_id)
        if not t:
            raise HTTPException(status_code=404, detail="track not found")
        seed = t["youtube_id"]
        if not seed:   # a local file: find the same song in the catalogue to seed from
            hits = await ytmusic.search_songs(f"{t['artist_name'] or ''} {t['title']}".strip(), 5)
            seed = hits[0]["video_id"] if hits else None
    if not seed or not _VID_RE.match(seed):
        raise HTTPException(status_code=404, detail="no radio seed for this song")
    try:
        tracks = await ytmusic.radio(seed)
    except Exception as err:  # noqa: BLE001
        raise _upstream_error(err) from err
    return {"seed": seed, "tracks": await annotate(tracks)}


@router.get("/home")
async def home() -> dict:
    """Recommendation shelves: personal (from your plays) + YouTube Music's own + charts."""
    country = await _country()
    async with get_engine().begin() as conn:
        recent = await fetch_one(conn, """
            SELECT v.id, v.title, v.artist_name, v.youtube_id FROM play_history h JOIN v_tracks v ON v.id = h.track_id
             ORDER BY h.started_at DESC LIMIT 1
        """)
        top_artist = await fetch_one(conn, """
            SELECT a.name, sum(t.play_count) AS plays FROM tracks t JOIN artists a ON a.id = t.primary_artist_id
             WHERE t.play_count > 0 GROUP BY a.name ORDER BY plays DESC LIMIT 1
        """)

    async def because():
        if not recent:
            return None
        seed = recent["youtube_id"]
        if not seed:
            hits = await ytmusic.search_songs(f"{recent['artist_name'] or ''} {recent['title']}".strip(), 3)
            seed = hits[0]["video_id"] if hits else None
        if not seed:
            return None
        return {"title": f"Because you listened to {recent['title']}", "kind": "tracks",
                "tracks": (await ytmusic.radio(seed))[:16]}

    async def more_like():
        if not top_artist:
            return None
        songs = await ytmusic.search_songs(top_artist["name"], 20)
        return {"title": f"More from {top_artist['name']}", "kind": "tracks", "tracks": songs[:16]} if songs else None

    results = await asyncio.gather(because(), more_like(), ytmusic.home_sections(6), ytmusic.charts(country),
                                   return_exceptions=True)
    sections: list[dict] = []
    for r in results[:2]:
        if isinstance(r, dict):
            sections.append(r)
        elif isinstance(r, Exception):
            log.info("personal shelf failed: %s", r)
    if isinstance(results[2], list):
        for s in results[2]:
            if s["tracks"]:
                sections.append({"title": s["title"], "kind": "tracks", "tracks": s["tracks"][:16]})
            if s["albums"]:
                sections.append({"title": s["title"], "kind": "albums", "albums": s["albums"][:16]})
            if s["playlists"]:
                sections.append({"title": s["title"], "kind": "playlists", "playlists": s["playlists"][:16]})
    if isinstance(results[3], dict):
        ch = results[3]
        if ch["playlists"]:
            sections.append({"title": "Trending now", "kind": "playlists", "playlists": ch["playlists"]})
        if ch["artists"]:
            sections.append({"title": "Top artists right now", "kind": "artists", "artists": ch["artists"]})
    if not sections and all(isinstance(r, Exception) or r is None for r in results):
        raise _upstream_error(next((r for r in results if isinstance(r, Exception)), RuntimeError("no data")))
    seen: set[tuple[str, str]] = set()
    sections = [s for s in sections if (key := (s["title"], s["kind"])) not in seen and not seen.add(key)]
    all_tracks = [t for s in sections if s["kind"] == "tracks" for t in s["tracks"]]
    await annotate(all_tracks)
    return {"country": country, "sections": sections}


# --- resolve: make a catalogue song playable -----------------------------------------------
class ResolveBody(BaseModel):
    video_id: str
    title: str | None = Field(default=None, max_length=500)
    artists: list[dict] = Field(default_factory=list)
    album_title: str | None = Field(default=None, max_length=500)
    thumbnail: str | None = Field(default=None, max_length=2000)
    duration_ms: int | None = Field(default=None, ge=0)
    video_type: str = "song"
    explicit: bool = False

    @field_validator("video_id")
    @classmethod
    def _vid(cls, v: str) -> str:
        if not _VID_RE.match(v):
            raise ValueError("invalid video id")
        return v


_resolve_locks: dict[str, asyncio.Lock] = {}
_bg_tasks: set[asyncio.Task] = set()


async def _track_for_video(vid: str) -> dict | None:
    async with get_engine().begin() as conn:
        return await fetch_one(conn, """
            SELECT v.* FROM media_sources ms JOIN v_tracks v ON v.id = ms.track_id
             WHERE ms.origin = 'remote' AND ms.extractor = 'youtube' AND ms.remote_id = :vid
             ORDER BY ms.id LIMIT 1
        """, vid=vid)


async def _enrich(http: httpx.AsyncClient, track: dict, video_type: str) -> None:
    """Background: synced lyrics + the official music video (enables Video mode)."""
    tid = track["id"]
    try:
        async with get_engine().begin() as conn:
            has_lyrics = await fetch_val(conn, "SELECT 1 FROM lyrics WHERE track_id = :t", t=tid)
        if not has_lyrics and track.get("artist_name"):
            found = await fetch_lrclib(http, title=track["title"], artist=track["artist_name"].split(",")[0],
                                       duration_ms=track.get("duration_ms"))
            if found:
                async with get_engine().begin() as conn:
                    await catalog_db.upsert_lyrics(conn, tid, synced=found["synced_lines"],
                                                   plain=found["plain_text"], source="remote")
        if video_type == "song" and not track.get("has_video"):
            mv = await ytmusic.find_music_video(track["title"], track.get("artist_name"))
            if mv:
                await importer.import_url(
                    http, f"https://www.youtube.com/watch?v={mv['video_id']}", kinds=("video",), track_id=tid,
                    fetch_lyrics=False, in_library=False,
                    meta_override={"title": track["title"], "artist": (track.get("artist_name") or "").split(",")[0],
                                   "album": track.get("album_title")},
                )
                log.info("attached music video %s to track %s", mv["video_id"], tid)
    except Exception as err:  # noqa: BLE001 - enrichment is best effort
        log.info("enrich of track %s failed: %s", tid, err)


@router.post("/resolve")
async def resolve(body: ResolveBody, http: httpx.AsyncClient = Depends(get_http)) -> dict:
    """Turn a catalogue item into a playable track (idempotent, ~2-4 s first time)."""
    vid = body.video_id
    lock = _resolve_locks.setdefault(vid, asyncio.Lock())
    async with lock:
        track = await _track_for_video(vid)
        created = False
        if not track:
            names = [a.get("name") for a in body.artists if a.get("name")]
            override = {
                "title": body.title, "artist": names[0] if names else None, "featured": names[1:],
                "album": body.album_title,
                "thumbnail": body.thumbnail if body.thumbnail and _allowed_thumb(body.thumbnail) else None,
                "duration_ms": body.duration_ms,
            }
            kinds = ("audio", "video") if body.video_type == "video" else ("audio",)
            try:
                res = await importer.import_url(http, f"https://www.youtube.com/watch?v={vid}", kinds=kinds,
                                                fetch_lyrics=False, in_library=False, meta_override=override)
            except resolver.ResolveError as err:
                raise HTTPException(status_code=422, detail=str(err)) from err
            if body.explicit:
                async with get_engine().begin() as conn:
                    await execute(conn, "UPDATE tracks SET explicit = true WHERE id = :id", id=res["track"]["id"])
            track = await _track_for_video(vid) or res["track"]
            created = True
    if created or not track.get("has_video") or not track.get("has_synced_lyrics"):
        task = asyncio.create_task(_enrich(http, dict(track), body.video_type))
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
    if not lock.locked():
        _resolve_locks.pop(vid, None)
    return {**track, "video_id": vid, "created": created}


# --- thumbnails (same-origin so canvas colour extraction + offline caching work) -----------
_THUMB_HOSTS = ("googleusercontent.com", "ytimg.com", "ggpht.com")
_thumb_cache: OrderedDict[str, tuple[bytes, str]] = OrderedDict()


def _thumb_dir() -> Path:
    d = get_settings().cover_cache_dir.parent / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _allowed_thumb(url: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in _THUMB_HOSTS)


@router.get("/thumb")
async def thumb(u: str = Query(max_length=2000), http: httpx.AsyncClient = Depends(get_http)) -> Response:
    if not _allowed_thumb(u):
        raise HTTPException(status_code=400, detail="thumbnail host not allowed")
    hit = _thumb_cache.get(u)
    disk = _thumb_dir() / (hashlib.sha1(u.encode()).hexdigest() + ".img")
    if hit is None and disk.exists():   # survives server restarts
        data = await asyncio.to_thread(disk.read_bytes)
        hit = (data, "image/png" if data[1:4] == b"PNG" else "image/jpeg")
        _thumb_cache[u] = hit
    if hit is None:
        # Some images reject the upscaled size we request: fall back to the
        # image's default size before giving up.
        candidates = [u]
        if "googleusercontent.com" in u or "ggpht.com" in u:
            candidates.append(u.rsplit("=", 1)[0] + "=s544")
        r = None
        for cand in candidates:
            try:
                r = await http.get(cand, timeout=15)
            except httpx.HTTPError:
                r = None
                continue
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                break
        ctype = r.headers.get("content-type", "image/jpeg") if r is not None else ""
        if r is None or r.status_code != 200 or not ctype.startswith("image/") or len(r.content) > 3_000_000:
            raise HTTPException(status_code=404, detail="thumbnail unavailable")
        hit = (r.content, ctype)
        _thumb_cache[u] = hit
        try:
            await asyncio.to_thread(disk.write_bytes, r.content)
        except OSError:
            pass
        while len(_thumb_cache) > 300:
            _thumb_cache.popitem(last=False)
    else:
        _thumb_cache.move_to_end(u)
    return Response(hit[0], media_type=hit[1], headers={"Cache-Control": "public, max-age=604800, immutable"})
