"""YouTube cookies managed from inside the app.

Cloud servers (Render, etc.) get "Sign in to confirm you're not a bot" from
YouTube. A cookies.txt exported from a signed-in browser fixes that. Instead of
a hosting-platform secret file, the user pastes it in Settings; it's stored in
the database (survives restarts on free hosting) and written to a private temp
file that yt-dlp reads.
"""
from __future__ import annotations

import asyncio
import os
import stat
import tempfile
from datetime import datetime
from pathlib import Path

from ..db import execute, fetch_one, get_engine

KEY = "youtube_cookies"
_path: Path | None = None
state: dict = {"configured": False, "updated_at": None, "last_test": None, "expired": False}

BOT_MARKERS = ("confirm you’re not a bot", "confirm you're not a bot", "sign in to confirm")
EXPIRED_MARKER = "cookies are no longer valid"
EXPIRED_MESSAGE = ("Your backup cookies have expired (YouTube cancelled that sign-in). "
                   "Export a fresh cookies.txt and save it in Settings → YouTube playback → Advanced.")


def is_bot_check(message: str) -> bool:
    m = (message or "").lower()
    return any(k in m for k in BOT_MARKERS)


def is_expired(message: str) -> bool:
    return EXPIRED_MARKER in (message or "").lower()


def mark_expired() -> None:
    state["expired"] = True


async def sync_rotated() -> bool:
    """YouTube refreshes its session cookies while they're used; yt-dlp writes
    the refreshed ones to our temp file. Copy them back to the database, or a
    restart would bring back the old (soon cancelled) set."""
    if not _path or not _path.exists() or state.get("expired"):
        return False
    text = await asyncio.to_thread(_path.read_text, encoding="utf-8")
    try:
        text = validate(text)
    except ValueError:
        return False
    async with get_engine().begin() as conn:
        res = await execute(conn, "UPDATE app_settings SET value = :v WHERE key = :k AND value <> :v", k=KEY, v=text)
    return res > 0


def current_path() -> str | None:
    return str(_path) if _path and _path.exists() else None


def validate(text: str) -> str:
    """Accept Netscape cookies.txt content that includes YouTube/Google cookies."""
    text = text.replace("\r\n", "\n").strip()
    if len(text) > 200_000:
        raise ValueError("That's too large for a cookies file.")
    rows = [ln for ln in text.splitlines() if ln and not ln.startswith("#") or ln.startswith("#HttpOnly_")]
    yt = [ln for ln in rows if "youtube.com" in ln or ".google." in ln]
    if not yt or not any(len(ln.split("\t")) >= 7 for ln in yt):
        raise ValueError("This doesn't look like a cookies.txt export with YouTube cookies "
                         "(Netscape format, exported while on youtube.com).")
    if not text.startswith("# Netscape") and not text.startswith("# HTTP Cookie File"):
        text = "# Netscape HTTP Cookie File\n" + text
    return text + "\n"


def _write(text: str) -> Path:
    global _path
    fd, name = tempfile.mkstemp(prefix="wonderplay-yt-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(name, stat.S_IRUSR | stat.S_IWUSR)
    old, _path = _path, Path(name)
    if old and old.exists():
        old.unlink(missing_ok=True)
    return _path


async def load() -> None:
    """Startup: restore cookies saved in the database."""
    async with get_engine().begin() as conn:
        row = await fetch_one(conn, "SELECT value, updated_at FROM app_settings WHERE key = :k", k=KEY)
    if row:
        await asyncio.to_thread(_write, row["value"])
        state.update(configured=True, updated_at=row["updated_at"])


async def save(text: str) -> None:
    clean = validate(text)
    async with get_engine().begin() as conn:
        await execute(conn, """
            INSERT INTO app_settings (key, value, updated_at) VALUES (:k, :v, now())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """, k=KEY, v=clean)
    await asyncio.to_thread(_write, clean)
    state.update(configured=True, updated_at=datetime.now().astimezone(), last_test=None, expired=False)


async def clear() -> None:
    global _path
    async with get_engine().begin() as conn:
        await execute(conn, "DELETE FROM app_settings WHERE key = :k", k=KEY)
    if _path and _path.exists():
        _path.unlink(missing_ok=True)
    _path = None
    state.update(configured=False, updated_at=None, last_test=None, expired=False)
