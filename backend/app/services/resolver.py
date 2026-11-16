"""yt-dlp integration: search, stream-URL resolution, import, and download.

Song/Video architecture: every remote video is stored as TWO media_sources:
  * kind='audio' -> an audio-only stream (AAC/M4A preferred: plays everywhere, incl. iOS)
  * kind='video' -> a video-only stream (H.264 preferred) up to the user's max height
The player keeps the audio element playing and overlays a muted <video>
synced to it, so toggling Song <-> Video never interrupts the sound, and HD
works without ffmpeg (no muxing needed).
"""
from __future__ import annotations

import asyncio
import logging
import os
import queue
import shutil
import tempfile
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import yt_dlp

from ..config import get_settings

log = logging.getLogger("sonicvault.resolver")

_AUDIO_EXT_PREF = {"m4a": 2, "mp4": 1}       # AAC first for universal browser support
_VIDEO_CODEC_PREF = ("avc1", "vp09", "vp9", "av01")


class ResolveError(Exception):
    pass


# --- yt-dlp options -----------------------------------------------------------

_cookie_copy: str | None = None


def _writable_cookies(path: str) -> str | None:
    """yt-dlp writes cookies back to the file it was given. Hosting platforms
    mount secret files read-only (Render: /etc/secrets/...), so work on a copy."""
    global _cookie_copy
    src = Path(path)
    if not src.is_file():
        log.warning("YTDLP_COOKIES_FILE %s not found; continuing without cookies", path)
        return None
    if os.access(src, os.W_OK):
        return str(src)
    if _cookie_copy is None:
        dst = Path(tempfile.gettempdir()) / "wonderplay-yt-cookies.txt"
        shutil.copyfile(src, dst)
        _cookie_copy = str(dst)
    return _cookie_copy

def _base_opts(**extra) -> dict:
    s = get_settings()
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 20,
        "format": "bestaudio/best",   # just needs to succeed; we pick formats ourselves
        "js_runtimes": {s.ytdlp_js_runtime: {}} if s.ytdlp_js_runtime else {},
    }
    if s.ytdlp_cookies_file and (cookies := _writable_cookies(s.ytdlp_cookies_file)):
        opts["cookiefile"] = cookies
    if s.ffmpeg_location:
        opts["ffmpeg_location"] = s.ffmpeg_location
    opts.update(extra)
    return opts


# --- search ---------------------------------------------------------------------

def _search_sync(query: str, limit: int) -> list[dict]:
    opts = _base_opts(extract_flat="in_playlist")
    with yt_dlp.YoutubeDL(opts) as ydl:
        res = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    out = []
    for e in (res or {}).get("entries") or []:
        if not e or not e.get("id"):
            continue
        thumbs = e.get("thumbnails") or []
        out.append({
            "id": e["id"],
            "url": e.get("url") or f"https://www.youtube.com/watch?v={e['id']}",
            "title": e.get("title"),
            "channel": e.get("channel") or e.get("uploader"),
            "duration_ms": int(e["duration"] * 1000) if e.get("duration") else None,
            "thumbnail": thumbs[-1]["url"] if thumbs else None,
            "view_count": e.get("view_count"),
        })
    return out


async def search(query: str, limit: int = 15) -> list[dict]:
    return await asyncio.to_thread(_search_sync, query, limit)


# --- format selection --------------------------------------------------------------

def _is_direct(f: dict) -> bool:
    # Progressive HTTPS only: HLS/DASH manifests can't be range-proxied as one file.
    return bool(f.get("url")) and (f.get("protocol") or "https") in ("https", "http")


def pick_audio(formats: list[dict]) -> dict | None:
    cands = [f for f in formats if _is_direct(f)
             and f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")]
    if not cands:
        cands = [f for f in formats if _is_direct(f) and f.get("acodec") not in (None, "none")]
    if not cands:
        return None
    return max(cands, key=lambda f: (
        _AUDIO_EXT_PREF.get(f.get("ext") or "", 0),
        "drc" not in (f.get("format_id") or "").lower(),   # skip dynamic-range-compressed variants
        f.get("abr") or f.get("tbr") or 0,
    ))


def pick_video(formats: list[dict], max_height: int) -> dict | None:
    def ok(f: dict) -> bool:
        return (_is_direct(f) and f.get("vcodec") not in (None, "none")
                and (f.get("height") or 0) <= max_height and (f.get("height") or 0) > 0)

    def codec_rank(f: dict) -> int:
        vc = (f.get("vcodec") or "").lower()
        for i, prefix in enumerate(_VIDEO_CODEC_PREF):
            if vc.startswith(prefix):
                return len(_VIDEO_CODEC_PREF) - i
        return 0

    video_only = [f for f in formats if ok(f) and f.get("acodec") in (None, "none")]
    cands = video_only or [f for f in formats if ok(f)]  # fall back to muxed
    if not cands:
        return None
    return max(cands, key=lambda f: (f.get("height") or 0, codec_rank(f), f.get("fps") or 0, f.get("tbr") or 0))


def _expiry_from_url(url: str) -> datetime:
    ttl = get_settings().ytdlp_stream_ttl_seconds
    exp = time.time() + ttl
    try:
        q = parse_qs(urlparse(url).query)
        if "expire" in q:
            exp = min(exp, int(q["expire"][0]))
    except (ValueError, IndexError):
        pass
    return datetime.fromtimestamp(exp - 300, tz=timezone.utc)  # 5 min safety margin


def format_to_source(f: dict, kind: str) -> dict:
    """Map a yt-dlp format dict onto media_sources columns."""
    ext = f.get("ext")
    is_video = kind == "video"
    mime = f"{'video' if is_video else 'audio'}/{'mp4' if ext in ('mp4', 'm4a') else (ext or 'mp4')}"
    w, h = (f.get("width"), f.get("height")) if is_video else (None, None)
    if not (w and h):
        w = h = None
    br = f.get("abr") if not is_video else f.get("vbr") or f.get("tbr")
    return {
        "format_id": f.get("format_id"),
        "cached_stream_url": f["url"],
        "stream_expires_at": _expiry_from_url(f["url"]),
        "stream_headers": f.get("http_headers") or {},
        "mime_type": mime,
        "container": ext,
        "audio_codec": None if f.get("acodec") in (None, "none") else f.get("acodec"),
        "video_codec": None if f.get("vcodec") in (None, "none") else f.get("vcodec"),
        "bitrate_kbps": int(br) if br and br > 0 else None,
        "sample_rate_hz": f.get("asr") or None,
        "channels": f.get("audio_channels") or None,
        "width": w, "height": h,
        "fps": f.get("fps") if is_video and f.get("fps") else None,
        "file_size_bytes": f.get("filesize") or None,
    }


# --- metadata cleanup ---------------------------------------------------------------

_JUNK = re.compile(
    r"\s*[\(\[]\s*(?:official\s*(?:music\s*)?(?:video|audio|lyric(?:s)?\s*video|visuali[sz]er|mv)"
    r"|(?:lyric(?:s)?|audio|video)(?:\s*video)?|hd|hq|4k|remastered(?:\s*\d{4})?|explicit|mv|m/v)\s*[\)\]]",
    re.IGNORECASE,
)
_TRAILER = re.compile(r"\s*[|｜]\s*.*$")


def clean_metadata(info: dict) -> dict:
    """Best-effort artist/title/album from a yt-dlp info dict."""
    title = info.get("track") or info.get("title") or "Untitled"
    artist = info.get("artist") or (info.get("artists") or [None])[0] or info.get("creator")
    if not info.get("track"):
        title = _TRAILER.sub("", _JUNK.sub("", title)).strip()
        if " - " in title and not artist:
            artist, title = (s.strip() for s in title.split(" - ", 1))
        elif " - " in title and artist and title.lower().startswith(artist.lower()):
            title = title.split(" - ", 1)[1].strip()
    if artist and "," in artist and info.get("artists"):
        artist = info["artists"][0]
    if not artist:
        ch = info.get("channel") or info.get("uploader") or ""
        artist = re.sub(r"\s*-\s*Topic$|VEVO$", "", ch).strip() or None
    year = info.get("release_year")
    if not year and info.get("upload_date"):
        year = int(info["upload_date"][:4])
    thumbs = sorted(
        (t for t in info.get("thumbnails") or [] if t.get("url")),
        key=lambda t: (t.get("preference") or 0, (t.get("width") or 0) * (t.get("height") or 0)),
    )
    return {
        "title": title or "Untitled",
        "artist": artist,
        "album": info.get("album"),
        "release_year": year if year and 1000 <= year <= 9999 else None,
        "duration_ms": int(info["duration"] * 1000) if info.get("duration") else None,
        "thumbnail": info.get("thumbnail") or (thumbs[-1]["url"] if thumbs else None),
        "genre": (info.get("genres") or [None])[0],
    }


# --- extraction -----------------------------------------------------------------------

# Long-lived extractors. yt-dlp keeps YouTube's ~2-3 MB player script in
# memory per YoutubeDL instance; a fresh instance per song re-downloads it
# every time (20-70 s on a throttled connection). Instances aren't thread-safe,
# so each is checked out of a small pool for the duration of one extraction.
_POOL_SIZE = 2
_pool: "queue.Queue[yt_dlp.YoutubeDL]" = queue.Queue()
_pool_created = 0
_pool_lock = threading.Lock()


def _youtube_args(fast: bool) -> dict:
    s = get_settings()
    args: dict = {}
    if fast:
        clients = [c.strip() for c in s.ytdlp_player_clients.split(",") if c.strip()]
        if clients:
            args["player_client"] = clients
    if s.ytdlp_player_js_variant:
        args["player_js_variant"] = [s.ytdlp_player_js_variant]
    return {"youtube": args} if args else {}


def _checkout() -> yt_dlp.YoutubeDL:
    global _pool_created
    try:
        return _pool.get_nowait()
    except queue.Empty:
        pass
    with _pool_lock:
        if _pool_created < _POOL_SIZE:
            _pool_created += 1
            return yt_dlp.YoutubeDL(_base_opts(extractor_args=_youtube_args(fast=True)))
    return _pool.get()   # all busy: wait for one


def _has_direct_formats(info: dict) -> bool:
    return any(_is_direct(f) and (f.get("acodec") not in (None, "none") or f.get("vcodec") not in (None, "none"))
               for f in info.get("formats") or [info])


def _extract_sync(url: str) -> dict:
    ydl = _checkout()
    try:
        info = ydl.extract_info(url, download=False)
        if info and info.get("_type") != "playlist" and not _has_direct_formats(info):
            raise yt_dlp.utils.DownloadError("fast client returned no direct formats")
    except yt_dlp.utils.DownloadError as e:
        # The fast client can be blocked for some videos: retry once with
        # yt-dlp's own client selection (slower, but broader).
        log.info("fast extraction failed for %s (%s); retrying with default clients", url, str(e)[:120])
        try:
            with yt_dlp.YoutubeDL(_base_opts(extractor_args=_youtube_args(fast=False))) as slow:
                info = slow.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as e2:
            raise ResolveError(str(e2).replace("ERROR: ", "")) from e2
    finally:
        _pool.put(ydl)
    if not info:
        raise ResolveError("no information returned")
    if info.get("_type") == "playlist":
        entries = [e for e in info.get("entries") or [] if e]
        if not entries:
            raise ResolveError("playlist is empty")
        info = entries[0]
    if info.get("is_live"):
        raise ResolveError("live streams are not supported")
    return info


async def extract(url: str) -> dict:
    return await asyncio.to_thread(_extract_sync, url)


async def warm_up() -> None:
    """Load YouTube's player script into the extractor pool at startup so the
    first song a user plays doesn't pay for that download."""
    try:
        t = time.time()
        await extract("https://www.youtube.com/watch?v=jNQXAC9IVRw")
        log.info("yt-dlp warm-up done in %.1fs", time.time() - t)
    except Exception as err:  # noqa: BLE001 - best effort
        log.info("yt-dlp warm-up skipped: %s", err)


def page_url_for(info: dict) -> str:
    return info.get("webpage_url") or info.get("original_url") or info.get("url")


# Per-source locks so a burst of range requests triggers only one re-resolve.
_refresh_locks: dict[int, asyncio.Lock] = {}


def refresh_lock(source_id: int) -> asyncio.Lock:
    return _refresh_locks.setdefault(source_id, asyncio.Lock())


async def resolve_format(page_url: str, kind: str, max_height: int) -> dict:
    info = await extract(page_url)
    fmts = info.get("formats") or [info]
    f = pick_audio(fmts) if kind == "audio" else pick_video(fmts, max_height)
    if not f:
        raise ResolveError(f"no playable {kind} format found")
    return format_to_source(f, kind)


async def fetch_bytes(client: httpx.AsyncClient, url: str, limit: int = 15 * 1024 * 1024) -> bytes | None:
    try:
        r = await client.get(url, timeout=15)
        if r.status_code == 200 and len(r.content) <= limit:
            return r.content
    except httpx.HTTPError:
        pass
    return None


# --- download (offline copy on the server) -----------------------------------------------

def _download_sync(page_url: str, format_id: str, dest_dir: Path, stem: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    opts = _base_opts(
        skip_download=False,
        format=format_id,
        outtmpl=str(dest_dir / f"{stem}.%(ext)s"),
        overwrites=True,
        noprogress=True,
        http_chunk_size=10 * 1024 * 1024,
        fixup="never",  # no ffmpeg needed; DASH m4a/mp4 play fine in browsers
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(page_url, download=True)
        path = Path(ydl.prepare_filename(info))
    if not path.exists():
        raise ResolveError("download finished but file is missing")
    return path


async def download(page_url: str, format_id: str, dest_dir: Path, stem: str) -> Path:
    return await asyncio.to_thread(_download_sync, page_url, format_id, dest_dir, stem)


def needs_refresh(src: dict) -> bool:
    exp = src.get("stream_expires_at")
    return not src.get("cached_stream_url") or exp is None or exp <= datetime.now(timezone.utc) + timedelta(seconds=60)
