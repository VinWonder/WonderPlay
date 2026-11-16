"""YouTube Music catalogue (search, artists, albums, playlists, radio, home)
via ytmusicapi, normalised into the shapes the WonderPlay UI renders.

ytmusicapi is synchronous; every call runs in a worker thread and results are
cached in memory (catalogue data changes slowly, and YouTube calls take 0.3-3 s).
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

from ytmusicapi import YTMusic

log = logging.getLogger("wonderplay.ytmusic")

_client: YTMusic | None = None
_client_lock = threading.Lock()


def _yt() -> YTMusic:
    global _client
    with _client_lock:
        if _client is None:
            _client = YTMusic()
        return _client


# --- tiny TTL cache --------------------------------------------------------------
class _Cache:
    def __init__(self, max_items: int = 400):
        self.data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self.max = max_items

    def get(self, key: str):
        hit = self.data.get(key)
        if not hit or hit[0] < time.time():
            return None
        self.data.move_to_end(key)
        return hit[1]

    def put(self, key: str, value: Any, ttl: float) -> None:
        self.data[key] = (time.time() + ttl, value)
        self.data.move_to_end(key)
        while len(self.data) > self.max:
            self.data.popitem(last=False)


_cache = _Cache()


async def cached(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    hit = _cache.get(key)
    if hit is not None:
        return hit
    value = await asyncio.to_thread(fn)
    _cache.put(key, value, ttl)
    return value


# --- normalisation ------------------------------------------------------------------
_SIZE_RE = re.compile(r"=w\d+-h\d+[^/]*$|=s\d+[^/]*$")


def big_thumb(thumbs: list[dict] | None, size: int = 544) -> str | None:
    """Best thumbnail URL, upscaled for googleusercontent art (=w60-h60 -> =w544-h544)."""
    if not thumbs:
        return None
    url = (thumbs[-1] or {}).get("url")
    if not url:
        return None
    if "googleusercontent.com" in url or "ggpht.com" in url:
        url = _SIZE_RE.sub(f"=w{size}-h{size}-l90-rj", url)
    return url


def _secs(text: str | None) -> int | None:
    if not text or not re.fullmatch(r"\d+(:\d{1,2}){1,2}", text.strip()):
        return None
    total = 0
    for part in text.strip().split(":"):
        total = total * 60 + int(part)
    return total


def norm_artists(raw: list[dict] | None) -> list[dict]:
    out = []
    for a in raw or []:
        name = (a or {}).get("name")
        # ytmusicapi sometimes puts "1.2M views" / "Song" in the artists list
        if not name or re.search(r"\b(views?|plays?)$", name, re.I) or name in ("Song", "Video", "Album", "Single", "EP"):
            continue
        out.append({"name": name, "id": a.get("id")})
    return out


def norm_track(x: dict, *, album: dict | None = None, album_thumb: str | None = None,
               artists_fallback: list[dict] | None = None) -> dict | None:
    vid = x.get("videoId")
    if not vid or x.get("isAvailable") is False:
        return None
    artists = norm_artists(x.get("artists")) or (artists_fallback or [])
    alb = x.get("album") or album or {}
    if isinstance(alb, str):          # album pages give the album as a plain name
        alb = {"name": alb, "id": (album or {}).get("id")}
    secs = x.get("duration_seconds") or _secs(x.get("duration") or x.get("length"))
    vtype = x.get("videoType") or ""
    is_video = vtype.endswith(("OMV", "UGC", "OFFICIAL_SOURCE_MUSIC")) and not vtype.endswith("ATV")
    thumb = big_thumb(x.get("thumbnails") or x.get("thumbnail")) or album_thumb
    return {
        "id": None,
        "video_id": vid,
        "title": x.get("title") or "Untitled",
        "artists": artists,
        "artist_name": ", ".join(a["name"] for a in artists) or None,
        "artist_browse_id": artists[0]["id"] if artists else None,
        "album_title": alb.get("name") or alb.get("title"),
        "album_browse_id": alb.get("id") or alb.get("browseId"),
        "thumbnail": thumb,
        "duration_ms": int(secs * 1000) if secs else None,
        "explicit": bool(x.get("isExplicit")),
        "video_type": "video" if is_video else "song",
        "has_audio": True,
        "has_video": is_video,
        "is_favorite": False,
        "source": "catalog",
    }


def norm_album(x: dict) -> dict | None:
    bid = x.get("browseId") or x.get("audioPlaylistId")
    if not bid:
        return None
    artists = norm_artists(x.get("artists"))
    return {
        "browse_id": bid,
        "title": x.get("title"),
        "artist_name": ", ".join(a["name"] for a in artists) or x.get("artist"),
        "artist_browse_id": artists[0]["id"] if artists else None,
        "year": x.get("year"),
        "type": (x.get("type") or "Album"),
        "thumbnail": big_thumb(x.get("thumbnails")),
    }


def norm_artist(x: dict) -> dict | None:
    bid = x.get("browseId") or ((x.get("artists") or [{}])[0] or {}).get("id")
    name = x.get("artist") or x.get("title") or ((x.get("artists") or [{}])[0] or {}).get("name")
    if not bid or not name:
        return None
    return {"browse_id": bid, "name": name, "thumbnail": big_thumb(x.get("thumbnails"), 400),
            "subscribers": x.get("subscribers")}


def norm_playlist(x: dict) -> dict | None:
    pid = x.get("playlistId") or x.get("browseId")
    if not pid:
        return None
    if pid.startswith("VL"):
        pid = pid[2:]
    author = x.get("author")
    if isinstance(author, list):
        author = ", ".join(a.get("name", "") for a in author if a)
    return {"playlist_id": pid, "title": x.get("title"), "author": author or x.get("description"),
            "thumbnail": big_thumb(x.get("thumbnails")), "count": x.get("itemCount") or x.get("count")}


def _dedupe(tracks: list[dict | None]) -> list[dict]:
    seen, out = set(), []
    for t in tracks:
        if t and t["video_id"] not in seen:
            seen.add(t["video_id"])
            out.append(t)
    return out


# --- public API ----------------------------------------------------------------------------
async def search(q: str) -> dict:
    q = q.strip()

    def run_general():
        return _yt().search(q, limit=20)

    def run_songs():
        return _yt().search(q, filter="songs", limit=20)

    general, songs = await asyncio.gather(
        cached(f"s:{q.lower()}", 900, run_general),
        cached(f"ss:{q.lower()}", 900, run_songs),
        return_exceptions=True,
    )
    if isinstance(general, Exception) and isinstance(songs, Exception):
        raise general
    general = [] if isinstance(general, Exception) else general
    songs = [] if isinstance(songs, Exception) else songs

    out: dict[str, Any] = {"top": None, "songs": [], "videos": [], "albums": [], "artists": [], "playlists": []}
    for item in general:
        rt = item.get("resultType")
        if item.get("category") == "Top result" and out["top"] is None:
            if rt == "artist" and (a := norm_artist(item)):
                out["top"] = {"type": "artist", **a}
            elif rt in ("song", "video") and (t := norm_track(item)):
                out["top"] = {"type": "track", **t}
            elif rt == "album" and (al := norm_album(item)):
                out["top"] = {"type": "album", **al}
        if rt == "video" and (t := norm_track(item)):
            out["videos"].append(t)
        elif rt == "album" and (al := norm_album(item)):
            out["albums"].append(al)
        elif rt == "artist" and (a := norm_artist(item)):
            out["artists"].append(a)
        elif rt == "playlist" and (p := norm_playlist(item)):
            out["playlists"].append(p)
        elif rt == "song" and not songs and (t := norm_track(item)):
            out["songs"].append(t)
    out["songs"] += [norm_track(s) for s in songs]
    out["songs"] = _dedupe(out["songs"])
    out["videos"] = _dedupe(out["videos"])
    # the top-result artist shouldn't be listed twice
    if out["top"] and out["top"]["type"] == "artist":
        out["artists"] = [a for a in out["artists"] if a["browse_id"] != out["top"]["browse_id"]]
    return out


async def search_songs(q: str, limit: int = 10) -> list[dict]:
    raw = await cached(f"ss:{q.lower()}", 900, lambda: _yt().search(q, filter="songs", limit=limit))
    return _dedupe([norm_track(x) for x in raw])


async def search_videos(q: str, limit: int = 5) -> list[dict]:
    raw = await cached(f"sv:{q.lower()}", 900, lambda: _yt().search(q, filter="videos", limit=limit))
    return _dedupe([norm_track(x) for x in raw])


async def radio(video_id: str, limit: int = 30) -> list[dict]:
    def run():
        return _yt().get_watch_playlist(videoId=video_id, radio=True, limit=limit)

    raw = await cached(f"r:{video_id}", 1800, run)
    tracks = _dedupe([norm_track(x) for x in raw.get("tracks", [])])
    return [t for t in tracks if t["video_id"] != video_id]


async def album(browse_id: str) -> dict:
    a = await cached(f"al:{browse_id}", 3600, lambda: _yt().get_album(browse_id))
    thumb = big_thumb(a.get("thumbnails"))
    artists = norm_artists(a.get("artists"))
    info = {"name": a.get("title"), "id": browse_id}
    tracks = _dedupe([norm_track(t, album=info, album_thumb=thumb, artists_fallback=artists) for t in a.get("tracks", [])])
    for t in tracks:            # album tracks often lack per-track art/album
        t["thumbnail"] = t["thumbnail"] or thumb
        t["album_title"], t["album_browse_id"] = a.get("title"), browse_id
    return {
        "browse_id": browse_id, "title": a.get("title"), "type": a.get("type") or "Album", "year": a.get("year"),
        "artist_name": ", ".join(x["name"] for x in artists) or None,
        "artist_browse_id": artists[0]["id"] if artists else None,
        "thumbnail": thumb, "description": a.get("description"),
        "duration_ms": (a.get("duration_seconds") or 0) * 1000 or None, "tracks": tracks,
    }


async def playlist(playlist_id: str) -> dict:
    p = await cached(f"pl:{playlist_id}", 1800, lambda: _yt().get_playlist(playlist_id, limit=100))
    author = p.get("author")
    if isinstance(author, dict):
        author = author.get("name")
    return {
        "playlist_id": playlist_id, "title": p.get("title"), "author": author,
        "description": p.get("description"), "thumbnail": big_thumb(p.get("thumbnails")),
        "tracks": _dedupe([norm_track(t) for t in p.get("tracks", [])]),
    }


async def artist(browse_id: str, name_hint: str | None = None) -> dict:
    """Artist page. ytmusicapi's get_artist breaks whenever YouTube tweaks the
    page layout, so fall back to assembling the page from searches."""
    try:
        a = await cached(f"ar:{browse_id}", 3600, lambda: _yt().get_artist(browse_id))
    except Exception as err:   # noqa: BLE001 - parser errors come in many types
        log.info("get_artist(%s) failed (%s); using search fallback", browse_id, type(err).__name__)
        return await _artist_fallback(browse_id, name_hint)

    def section(key, fn):
        return [y for y in (fn(x) for x in (a.get(key) or {}).get("results", [])) if y]

    songs = section("songs", norm_track)
    if len(songs) < 5 and a.get("name"):
        songs = _dedupe(songs + [t for t in await search_songs(a["name"], 20)
                                 if t["artist_browse_id"] == browse_id])
    return {
        "browse_id": browse_id, "name": a.get("name") or name_hint, "description": a.get("description"),
        "thumbnail": big_thumb(a.get("thumbnails"), 900), "subscribers": a.get("subscribers"),
        "songs": songs[:20], "albums": section("albums", norm_album), "singles": section("singles", norm_album),
        "videos": section("videos", norm_track), "related": section("related", norm_artist),
    }


async def _artist_fallback(browse_id: str, name: str | None) -> dict:
    if not name:
        raise LookupError("artist name needed for fallback lookup")

    def run(filter_):
        return _yt().search(name, filter=filter_, limit=40)

    songs, albums, artists = await asyncio.gather(
        cached(f"ss:{name.lower()}", 900, lambda: run("songs")),
        cached(f"sa:{name.lower()}", 900, lambda: run("albums")),
        cached(f"sr:{name.lower()}", 900, lambda: run("artists")),
        return_exceptions=True,
    )
    songs = [] if isinstance(songs, Exception) else songs
    albums = [] if isinstance(albums, Exception) else albums
    artists = [] if isinstance(artists, Exception) else artists

    mine = lambda x: any((a or {}).get("id") == browse_id for a in x.get("artists") or [])  # noqa: E731
    me = next((a for a in artists if a.get("browseId") == browse_id), None)
    all_albums = [norm_album(x) for x in albums if mine(x)]
    return {
        "browse_id": browse_id, "name": name, "description": None,
        "thumbnail": big_thumb((me or {}).get("thumbnails"), 900), "subscribers": (me or {}).get("subscribers"),
        "songs": _dedupe([norm_track(x) for x in songs if mine(x)])[:20],
        "albums": [x for x in all_albums if x and (x["type"] or "").lower() not in ("single", "ep")],
        "singles": [x for x in all_albums if x and (x["type"] or "").lower() in ("single", "ep")],
        "videos": [],
        "related": [y for y in (norm_artist(x) for x in artists if x.get("browseId") != browse_id) if y][:12],
    }


async def moods() -> list[dict]:
    raw = await cached("moods", 86400, lambda: _yt().get_mood_categories())
    return [{"title": title, "items": [{"title": x["title"], "params": x["params"]} for x in items]}
            for title, items in raw.items()]


async def mood_playlists(params: str) -> list[dict]:
    raw = await cached(f"mp:{params}", 3600, lambda: _yt().get_mood_playlists(params))
    return [y for y in (norm_playlist(x) for x in raw) if y]


async def charts(country: str = "ZZ") -> dict:
    raw = await cached(f"ch:{country}", 3600, lambda: _yt().get_charts(country))
    vids = raw.get("videos")
    playlists = vids if isinstance(vids, list) else (vids or {}).get("items", [])
    return {
        "playlists": [y for y in (norm_playlist(x) for x in playlists) if y],
        "artists": [y for y in (norm_artist(x) for x in (raw.get("artists") or [])) if y][:20],
    }


async def home_sections(limit: int = 6) -> list[dict]:
    """YouTube Music's own (signed-out) home shelves: Quick picks, hits, new releases..."""
    raw = await cached("home", 1800, lambda: _yt().get_home(limit=limit))
    out = []
    for sec in raw:
        tracks, albums, playlists = [], [], []
        for x in sec.get("contents") or []:
            if not x:
                continue
            if x.get("videoId"):
                if t := norm_track(x):
                    tracks.append(t)
            elif x.get("playlistId") and not x.get("browseId", "").startswith("MPRE"):
                if p := norm_playlist(x):
                    playlists.append(p)
            elif x.get("browseId", "").startswith("MPRE"):
                if al := norm_album(x):
                    albums.append(al)
        if tracks or albums or playlists:
            out.append({"title": sec.get("title"), "tracks": _dedupe(tracks), "albums": albums, "playlists": playlists})
    return out


def _norm_text(s: str) -> str:
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s.lower())
    return re.sub(r"[^\w]+", " ", s).strip()


async def find_music_video(title: str, artist: str | None) -> dict | None:
    """Best official music video (OMV) for a song, or None."""
    if not artist:
        return None
    want = _norm_text(title)
    artist_l = artist.lower().split(",")[0].strip()
    for v in await search_videos(f"{artist} {title}", 6):
        names = (v["artist_name"] or "").lower()
        if want and want in _norm_text(v["title"]) and artist_l in names:
            return v
    return None
