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
from . import netproxy, ytcookies

log = logging.getLogger("sonicvault.resolver")

_AUDIO_EXT_PREF = {"m4a": 2, "mp4": 1}       # AAC first for universal browser support
_VIDEO_CODEC_PREF = ("avc1", "vp09", "vp9", "av01")


class ResolveError(Exception):
    pass


class BotCheckError(ResolveError):
    """YouTube refused this server ("Sign in to confirm you're not a bot")."""


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
    # Cookies pasted in the app (stored in the database) win over a cookies file.
    cookies = ytcookies.current_path() or (
        _writable_cookies(s.ytdlp_cookies_file) if s.ytdlp_cookies_file else None)
    if cookies:
        opts["cookiefile"] = cookies
    if netproxy.proxy_url:          # Cloudflare WARP on hosted servers
        opts["proxy"] = netproxy.proxy_url
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

# Routes: a route is (player client, network). YouTube judges each request by
# the client it imitates *and* the IP it comes from, so when one combination is
# refused ("confirm you're not a bot") another often still works. We try them
# in order and remember the winner, so later songs go straight to it.
VIA_WARP, VIA_DIRECT = "warp", "direct"
VIA_HEADER = "X-WP-Via"       # marks stream URLs that must be fetched without WARP
FALLBACK_CLIENTS = ("visionos", "web_embedded", "android_vr", "tv_simply", "mweb")
_PERMANENT = ("private video", "has been removed", "copyright", "members-only",
              "join this channel", "account associated with this video", "live event will begin")
_ROUTE_BUDGET_S = 75

Route = tuple  # (client: str | None, via: str)


def _known_clients() -> dict:
    try:
        from yt_dlp.extractor.youtube._base import INNERTUBE_CLIENTS
        return INNERTUBE_CLIENTS
    except Exception:  # noqa: BLE001 - yt-dlp internals moved: trust the names
        return {}


def _supports_cookies(client: str | None) -> bool:
    known = _known_clients()
    return client is None or not known or bool(known.get(client, {}).get("SUPPORTS_COOKIES"))


def routes() -> list[Route]:
    """All routes in preference order; a client of None = yt-dlp's own choice."""
    s = get_settings()
    known = _known_clients()
    clients: list[str | None] = []
    if ytcookies.current_path():
        clients.append(None)                     # signed in: yt-dlp's default clients
    for c in [c.strip() for c in s.ytdlp_player_clients.split(",")] + list(FALLBACK_CLIENTS):
        if c and c not in clients and (not known or c in known):   # yt-dlp drops retired clients
            clients.append(c)
    if None not in clients:
        clients.append(None)
    if ytcookies.current_path():
        clients.sort(key=lambda c: not _supports_cookies(c))     # stable: cookie clients first
    vias = [VIA_WARP, VIA_DIRECT] if netproxy.proxy_url else [VIA_DIRECT]
    return [(c, v) for v in vias for c in clients]


def route_name(route: Route | None) -> str:
    if not route:
        return "none"
    client, via = route
    return f"{client or 'default'} via {'Cloudflare WARP' if via == VIA_WARP else 'direct connection'}"


_preferred: Route | None = None
last_route: Route | None = None
last_attempts: list[dict] = []


def _route_opts(route: Route) -> dict:
    client, via = route
    s = get_settings()
    args: dict = {}
    if client:
        args["player_client"] = [client]
    if s.ytdlp_player_js_variant:
        args["player_js_variant"] = [s.ytdlp_player_js_variant]
    opts = _base_opts(extractor_args={"youtube": args} if args else {},
                      no_warnings=False, logger=_Collector())
    if via == VIA_DIRECT:
        opts.pop("proxy", None)
        if netproxy.proxy_url:
            # One sign-in seen from two addresses gets cancelled by YouTube:
            # with WARP up, cookies only ever travel through WARP.
            opts.pop("cookiefile", None)
    if not _supports_cookies(client):
        # yt-dlp would skip this client if handed cookies; run it signed out instead.
        opts.pop("cookiefile", None)
    return opts


class _Collector:
    """yt-dlp logger that keeps the warnings of the current extraction, which
    say *why* YouTube sent no audio (missing token, unsupported client...)."""

    def __init__(self):
        self.lines: list[str] = []

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        msg = str(msg).replace("WARNING: ", "").strip()
        if msg and msg not in self.lines:
            self.lines.append(msg)
            log.debug("yt-dlp: %s", msg)

    def error(self, msg):
        pass

    def take(self) -> list[str]:
        out, self.lines = self.lines[-4:], []
        return [m[:220] for m in out]


def _warnings(ydl) -> list[str]:
    lg = (getattr(ydl, "params", None) or {}).get("logger")
    return lg.take() if isinstance(lg, _Collector) else []


# Long-lived extractors. yt-dlp keeps YouTube's ~2-3 MB player script in
# memory per YoutubeDL instance; a fresh instance per song re-downloads it
# every time (20-70 s on a throttled connection). Instances aren't thread-safe,
# so each is checked out of a small pool for the duration of one extraction.
# The pool always serves the preferred route.
_POOL_SIZE = 2
_pool: "queue.Queue[yt_dlp.YoutubeDL]" = queue.Queue()
_pool_created = 0
_pool_lock = threading.Lock()
_generation = 0        # bumped when cookies/network/route change: pooled extractors are rebuilt


def reset_pool(*, forget_route: bool = True) -> None:
    """Drop pooled extractors so the next extraction picks up new settings."""
    global _pool_created, _generation, _preferred
    with _pool_lock:
        _generation += 1
        if forget_route:
            _preferred = None
        while True:
            try:
                _pool.get_nowait()
            except queue.Empty:
                break
        _pool_created = 0


def _current_preferred() -> Route:
    all_routes = routes()
    return _preferred if _preferred in all_routes else all_routes[0]


def _checkout() -> yt_dlp.YoutubeDL:
    global _pool_created
    try:
        return _pool.get_nowait()
    except queue.Empty:
        pass
    with _pool_lock:
        if _pool_created < _POOL_SIZE:
            _pool_created += 1
            route = _current_preferred()
            ydl = yt_dlp.YoutubeDL(_route_opts(route))
            ydl._wp_generation = _generation
            ydl._wp_route = route
            return ydl
    return _pool.get()   # all busy: wait for one


def _checkin(ydl: yt_dlp.YoutubeDL) -> None:
    global _pool_created
    if getattr(ydl, "_wp_generation", None) == _generation:
        _pool.put(ydl)
    else:
        with _pool_lock:
            _pool_created = max(0, _pool_created - 1)


def _has_direct_formats(info: dict) -> bool:
    return any(_is_direct(f) and (f.get("acodec") not in (None, "none") or f.get("vcodec") not in (None, "none"))
               for f in info.get("formats") or [info])


def _first_entry(info: dict | None) -> dict | None:
    if info and info.get("_type") == "playlist":
        entries = [e for e in info.get("entries") or [] if e]
        return entries[0] if entries else None
    return info


def _try(ydl: yt_dlp.YoutubeDL, url: str) -> dict:
    info = _first_entry(ydl.extract_info(url, download=False))
    if info is None:
        raise ResolveError("no information returned")
    if info.get("is_live"):
        raise ResolveError("live streams are not supported")
    if not _has_direct_formats(info):
        raise yt_dlp.utils.DownloadError("no direct formats from this client")
    return info


def _tag(info: dict, via: str) -> dict:
    """Stream URLs only work from the IP that resolved them: remember the network."""
    if via == VIA_DIRECT and netproxy.proxy_url:
        for f in info.get("formats") or [info]:
            f["http_headers"] = {**(f.get("http_headers") or {}), VIA_HEADER: VIA_DIRECT}
    return info


def _extract_sync(url: str, budget: float = _ROUTE_BUDGET_S) -> dict:
    global _preferred, last_route, last_attempts
    attempts: list[dict] = []
    errors: list[str] = []
    started = time.time()
    ydl = _checkout()
    first = getattr(ydl, "_wp_route", None) or _current_preferred()
    try:
        info = _try(ydl, url)
        _warnings(ydl)
        if (getattr(ydl, "params", None) or {}).get("cookiefile"):
            try:   # keep YouTube's refreshed session cookies (see ytcookies.sync_rotated)
                ydl.cookiejar.save()
            except Exception:  # noqa: BLE001
                pass
        last_route, last_attempts = first, [{"route": route_name(first), "ok": True}]
        return _tag(info, first[1])
    except yt_dlp.utils.DownloadError as e:
        errors.append(str(e).replace("ERROR: ", ""))
        attempts.append({"route": route_name(first), "ok": False, "error": errors[-1][:160],
                         "warnings": _warnings(ydl)})
    finally:
        _checkin(ydl)

    if any(p in errors[-1].lower() for p in _PERMANENT):
        last_attempts = attempts
        raise ResolveError(errors[-1])

    for route in routes():
        if route == first:
            continue
        if time.time() - started > budget:
            break
        log.info("extraction failed for %s on %s; trying %s", url, route_name(first), route_name(route))
        opts = _route_opts(route)
        try:
            with yt_dlp.YoutubeDL(opts) as other:
                info = _try(other, url)
        except yt_dlp.utils.DownloadError as e:
            errors.append(str(e).replace("ERROR: ", ""))
            attempts.append({"route": route_name(route), "ok": False, "error": errors[-1][:160],
                             "warnings": opts["logger"].take()})
            continue
        attempts.append({"route": route_name(route), "ok": True})
        last_route, last_attempts = route, attempts
        if route != _preferred:
            log.info("YouTube works on %s; using it from now on", route_name(route))
            _preferred = route
            reset_pool(forget_route=False)
        return _tag(info, route[1])

    last_attempts = attempts
    for a in attempts:
        log.warning("YouTube route failed: %s: %s", a["route"], a.get("error"))
    if any(ytcookies.is_expired(w) for a in attempts for w in a.get("warnings") or []):
        ytcookies.mark_expired()
        raise BotCheckError(ytcookies.EXPIRED_MESSAGE)
    if ytcookies.current_path():
        signed_in = [m for m, a in zip(errors, attempts) if not ytcookies.is_bot_check(m)]
        if signed_in:
            raise ResolveError("YouTube accepted the cookies but sent no playable audio: " + signed_in[0][:200])
    if any(ytcookies.is_bot_check(m) for m in errors):
        raise BotCheckError(
            "YouTube is blocking this server. Add backup cookies in Settings → YouTube playback → Advanced.")
    raise ResolveError(errors[0] if errors else "extraction failed")


async def extract(url: str, budget: float = _ROUTE_BUDGET_S) -> dict:
    return await asyncio.to_thread(_extract_sync, url, budget)


_DIAG_KEEP = ("[youtube]", "[debug] [youtube]", "[jsc", "[pot", "[debug] [jsc", "[debug] [pot",
              "warning", "error", "[debug] yt-dlp version", "[debug] python", "[debug] js", "[debug] proxy",
              "formats", "cookie", "po token", "sabr", "challenge")


class _Trace(_Collector):
    def debug(self, msg):
        self.lines.append(str(msg))

    info = debug

    def warning(self, msg):
        self.lines.append("WARNING: " + str(msg).replace("WARNING: ", ""))

    def error(self, msg):
        self.lines.append(str(msg))


def _diagnose_sync(url: str, route: Route) -> dict:
    """One verbose extraction on `route`, keeping only the lines that explain
    what YouTube sent back (formats, tokens, JS challenges, warnings)."""
    trace = _Trace()
    opts = _route_opts(route)
    opts.update(logger=trace, verbose=True, quiet=False, ignore_no_formats_error=True, format=None)
    formats: list[str] = []
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = _first_entry(ydl.extract_info(url, download=False)) or {}
        for f in info.get("formats") or []:
            kind = "audio" if f.get("vcodec") in (None, "none") else ("images" if f.get("ext") == "mhtml" else "video")
            formats.append(f"{f.get('format_id')} {kind} {f.get('ext')} {f.get('protocol')}")
    except Exception as err:  # noqa: BLE001 - diagnostics must not raise
        trace.lines.append(f"EXCEPTION: {type(err).__name__}: {err}")
    keep = [ln[:300] for ln in trace.lines
            if any(k in ln.lower() for k in _DIAG_KEEP) and "downloading webpage" not in ln.lower()]
    return {"route": route_name(route), "formats": formats[:25], "format_count": len(formats), "log": keep[-40:]}


async def diagnose(url: str) -> dict:
    route = next((r for r in routes() if r[0] is not None and _supports_cookies(r[0])), routes()[0])
    return await asyncio.to_thread(_diagnose_sync, url, route)


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
