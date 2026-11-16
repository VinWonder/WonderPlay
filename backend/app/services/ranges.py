"""HTTP byte-range streaming (RFC 9110 §14) for local files.

Browsers' <audio>/<video> elements request `Range: bytes=N-` whenever the user
seeks; answering with `206 Partial Content` makes scrubbing instant because
only the bytes around the new position are read and sent.
"""
from __future__ import annotations

import mimetypes
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path

import aiofiles
from fastapi import Request
from fastapi.responses import Response, StreamingResponse

CHUNK_SIZE = 256 * 1024  # 256 KiB reads: low latency on seek, few syscalls

_RANGE_RE = re.compile(r"^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$", re.IGNORECASE)

# Browsers need correct types; Python's table misses / mislabels some of these.
_EXTRA_TYPES = {
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".weba": "audio/webm",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def guess_mime(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    return _EXTRA_TYPES.get(ext) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"


class RangeNotSatisfiable(Exception):
    def __init__(self, size: int):
        self.size = size


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int  # inclusive

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def parse_range(header: str | None, size: int) -> ByteRange | None:
    """Parse a Range header against a resource of `size` bytes.

    Returns None when the whole resource should be sent (no header, a syntax
    we don't support, or a multi-range request; ignoring Range is always
    allowed by the RFC). Raises RangeNotSatisfiable for an out-of-bounds range.
    """
    if not header:
        return None
    if "," in header:  # multipart/byteranges: legal to ignore, browsers never send it for media
        return None
    m = _RANGE_RE.match(header)
    if not m:
        return None
    first, last = m.group(1), m.group(2)

    if first == "" and last == "":
        return None
    if first == "":  # suffix range: last N bytes
        suffix = int(last)
        if suffix == 0 or size == 0:
            raise RangeNotSatisfiable(size)
        return ByteRange(max(size - suffix, 0), size - 1)

    start = int(first)
    if last and int(last) < start:
        return None  # invalid syntax -> ignore the header
    if start >= size:
        raise RangeNotSatisfiable(size)
    end = int(last) if last else size - 1
    return ByteRange(start, min(end, size - 1))


def _etag_for(st: os.stat_result) -> str:
    return f'"{st.st_size:x}-{st.st_mtime_ns:x}"'


def _if_range_allows(if_range: str | None, etag: str, mtime: float) -> bool:
    """If-Range: honour Range only if the client's copy is still current."""
    if not if_range:
        return True
    if_range = if_range.strip()
    if if_range.startswith(('"', 'W/"')):
        return if_range == etag  # strong comparison required
    try:
        return int(parsedate_to_datetime(if_range).timestamp()) >= int(mtime)
    except (TypeError, ValueError):
        return False


async def _iter_file(path: Path, start: int, length: int) -> AsyncIterator[bytes]:
    async with aiofiles.open(path, "rb") as f:
        await f.seek(start)
        remaining = length
        while remaining > 0:
            chunk = await f.read(min(CHUNK_SIZE, remaining))
            if not chunk:  # file truncated underneath us
                break
            remaining -= len(chunk)
            yield chunk


def range_file_response(
    request: Request,
    path: Path,
    media_type: str | None = None,
    *,
    cache_control: str = "private, max-age=86400",
) -> Response:
    """Serve a local file with full Range / conditional-request support."""
    st = path.stat()
    size = st.st_size
    etag = _etag_for(st)
    media_type = media_type or guess_mime(path)
    base_headers = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Last-Modified": formatdate(st.st_mtime, usegmt=True),
        "Cache-Control": cache_control,
    }

    range_header = request.headers.get("range")

    # Plain revalidation (no Range): 304 if unchanged.
    inm = request.headers.get("if-none-match")
    if inm and not range_header and etag in [t.strip() for t in inm.split(",")]:
        return Response(status_code=304, headers=base_headers)

    if range_header and not _if_range_allows(request.headers.get("if-range"), etag, st.st_mtime):
        range_header = None  # stale validator -> send the full, current file

    try:
        br = parse_range(range_header, size)
    except RangeNotSatisfiable:
        return Response(
            status_code=416,
            headers={**base_headers, "Content-Range": f"bytes */{size}"},
        )

    if br is None:
        status, start, length = 200, 0, size
        headers = {**base_headers, "Content-Length": str(size)}
    else:
        status, start, length = 206, br.start, br.length
        headers = {
            **base_headers,
            "Content-Length": str(br.length),
            "Content-Range": f"bytes {br.start}-{br.end}/{size}",
        }

    if request.method == "HEAD":
        return Response(status_code=status, headers=headers, media_type=media_type)

    return StreamingResponse(
        _iter_file(path, start, length),
        status_code=status,
        headers=headers,
        media_type=media_type,
    )
