"""Lyrics: LRC parsing and optional LRCLIB lookup.

Synced lines are stored as [{"t": <ms>, "text": "..."}] sorted by t.
"""
from __future__ import annotations

import re

import httpx

_TIME_TAG = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_META_TAG = re.compile(r"^\[([a-zA-Z]+):(.*)\]\s*$")
# Enhanced-LRC word timings: <00:12.34>
_WORD_TAG = re.compile(r"<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>")

LRCLIB_URL = "https://lrclib.net/api/get"


def _to_ms(mm: str, ss: str, frac: str | None) -> int:
    ms = 0
    if frac:
        # "5" -> 500ms, "50" -> 500ms, "500" -> 500ms
        ms = int(frac.ljust(3, "0")[:3])
    return (int(mm) * 60 + int(ss)) * 1000 + ms


def parse_lrc(text: str) -> tuple[list[dict], str | None]:
    """Parse LRC text. Returns (synced_lines, plain_text).

    Handles repeated timestamps (`[00:12.00][01:30.00]chorus`), the `[offset:]`
    tag (positive = lyrics appear earlier, per the LRC spec), and strips
    enhanced word-level tags.
    """
    offset = 0
    lines: list[dict] = []
    plain: list[str] = []

    for raw in text.splitlines():
        raw = raw.strip().lstrip("﻿")
        if not raw:
            continue
        stamps = list(_TIME_TAG.finditer(raw))
        if not stamps:
            meta = _META_TAG.match(raw)
            if meta:
                if meta.group(1).lower() == "offset":
                    try:
                        offset = int(meta.group(2).strip())
                    except ValueError:
                        pass
                continue
            plain.append(raw)
            continue

        # text is whatever follows the last leading timestamp
        lyric = _WORD_TAG.sub("", raw[stamps[-1].end():]).strip()
        for s in stamps:
            t = _to_ms(s.group(1), s.group(2), s.group(3)) - offset
            lines.append({"t": max(t, 0), "text": lyric})
        plain.append(lyric)

    lines.sort(key=lambda x: x["t"])
    plain_text = "\n".join(p for p in plain if p) or None
    return lines, plain_text


def sylt_to_lines(sylt_text: list[tuple[str, int]]) -> list[dict]:
    """ID3 SYLT frame (format = milliseconds) -> synced lines."""
    out = [{"t": int(t), "text": (txt or "").strip()} for txt, t in sylt_text]
    out.sort(key=lambda x: x["t"])
    return out


async def fetch_lrclib(
    client: httpx.AsyncClient,
    *,
    title: str,
    artist: str,
    album: str | None = None,
    duration_ms: int | None = None,
) -> dict | None:
    """Look a track up on LRCLIB (free, no key). Returns
    {"synced_lines", "plain_text"} or None when nothing was found."""
    params = {"track_name": title, "artist_name": artist}
    if album:
        params["album_name"] = album
    if duration_ms:
        params["duration"] = str(round(duration_ms / 1000))
    try:
        resp = await client.get(LRCLIB_URL, params=params, timeout=10,
                                headers={"User-Agent": "WonderPlay/1.0"})
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    data = resp.json()
    synced, plain = [], data.get("plainLyrics") or None
    if data.get("syncedLyrics"):
        synced, plain_from_lrc = parse_lrc(data["syncedLyrics"])
        plain = plain or plain_from_lrc
    if not synced and not plain:
        return None
    return {"synced_lines": synced or None, "plain_text": plain}
