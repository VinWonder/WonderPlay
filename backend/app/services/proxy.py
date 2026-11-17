"""Range-aware reverse proxy for remote (yt-dlp resolved) streams.

Why proxy instead of redirecting the browser to the CDN URL?
  * Same origin -> the Web Audio AnalyserNode can read samples (cross-origin
    media without CORS headers yields silence to the visualizer).
  * The service worker (Phase 5) can cache the bytes for offline playback.
  * yt-dlp's required request headers (User-Agent etc.) are applied here.
  * googlevideo throttles open-ended `bytes=N-` requests, so we split them
    into bounded upstream chunks while presenting one seamless 206 to the client.
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
from fastapi import Request
from fastapi.responses import Response, StreamingResponse

from .ranges import _RANGE_RE

UPSTREAM_CHUNK = 8 * 1024 * 1024  # 8 MiB per upstream request (below googlevideo's throttle threshold)
_CONTENT_RANGE_RE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", re.IGNORECASE)

# Hop-by-hop or yt-dlp-internal headers that must not be forwarded upstream.
_SKIP_UPSTREAM = {"host", "range", "accept-encoding", "connection", "content-length", "x-wp-via"}


class UpstreamExpired(Exception):
    """Upstream rejected the URL (403/404/410): signed stream URL has expired."""


class UpstreamError(Exception):
    def __init__(self, status: int):
        super().__init__(f"upstream returned HTTP {status}")
        self.status = status


def _parse_client_range(header: str | None) -> tuple[int | None, int | None] | None:
    """Return (start, end) / (None, suffix_len) or None for 'no usable range'."""
    if not header or "," in header:
        return None
    m = _RANGE_RE.match(header)
    if not m or (m.group(1) == "" and m.group(2) == ""):
        return None
    if m.group(1) == "":
        return None, int(m.group(2))
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else None
    if end is not None and end < start:
        return None
    return start, end


async def _open(client: httpx.AsyncClient, url: str, headers: dict, start: int, end: int) -> httpx.Response:
    # identity encoding: byte offsets must refer to the raw media bytes.
    req = client.build_request(
        "GET", url, headers={**headers, "Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"}
    )
    resp = await client.send(req, stream=True)
    if resp.status_code in (403, 404, 410):
        await resp.aclose()
        raise UpstreamExpired()
    if resp.status_code == 416:
        await resp.aclose()
        raise UpstreamError(416)
    if resp.status_code not in (200, 206):
        await resp.aclose()
        raise UpstreamError(resp.status_code)
    return resp


async def _probe_size(client: httpx.AsyncClient, url: str, headers: dict) -> int:
    resp = await _open(client, url, headers, 0, 0)
    try:
        m = _CONTENT_RANGE_RE.search(resp.headers.get("content-range", ""))
        if m and m.group(3) != "*":
            return int(m.group(3))
        if resp.status_code == 200 and resp.headers.get("content-length"):
            return int(resp.headers["content-length"])
        raise UpstreamError(502)
    finally:
        await resp.aclose()


async def proxy_stream(
    request: Request,
    client: httpx.AsyncClient,
    url: str,
    upstream_headers: dict | None,
    *,
    media_type: str,
    known_size: int | None = None,
) -> Response:
    """Proxy `url` honouring the client's Range header.

    Raises UpstreamExpired *before* any bytes are sent so the caller can
    re-resolve the URL with yt-dlp and retry transparently.
    """
    headers = {k: v for k, v in (upstream_headers or {}).items() if k.lower() not in _SKIP_UPSTREAM}
    wanted = _parse_client_range(request.headers.get("range"))

    size = known_size
    if wanted is not None and wanted[0] is None:  # suffix range needs the total size first
        size = size or await _probe_size(client, url, headers)
        suffix = wanted[1] or 0
        if suffix == 0:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        wanted = (max(size - suffix, 0), size - 1)

    start = wanted[0] if wanted else 0
    req_end = wanted[1] if wanted else None

    if size is not None and start >= size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})

    first_end = start + UPSTREAM_CHUNK - 1
    if req_end is not None:
        first_end = min(first_end, req_end)
    if size is not None:
        first_end = min(first_end, size - 1)

    try:
        first = await _open(client, url, headers, start, first_end)
    except UpstreamError as e:
        if e.status == 416:
            return Response(status_code=416)
        raise

    # Upstream ignored Range and is sending everything: pass it straight through.
    if first.status_code == 200:
        out_headers = {"Accept-Ranges": "none"}
        if first.headers.get("content-length"):
            out_headers["Content-Length"] = first.headers["content-length"]

        async def passthrough() -> AsyncIterator[bytes]:
            try:
                async for chunk in first.aiter_bytes():
                    yield chunk
            finally:
                await first.aclose()

        if request.method == "HEAD":
            await first.aclose()
            return Response(status_code=200, headers=out_headers, media_type=media_type)
        return StreamingResponse(passthrough(), status_code=200, headers=out_headers, media_type=media_type)

    m = _CONTENT_RANGE_RE.search(first.headers.get("content-range", ""))
    if m and m.group(3) != "*":
        size = int(m.group(3))
    if size is None:
        await first.aclose()
        raise UpstreamError(502)

    end = size - 1 if req_end is None else min(req_end, size - 1)
    length = end - start + 1
    media_type = first.headers.get("content-type") or media_type

    out_headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Cache-Control": "no-store",
    }
    status = 206 if wanted is not None else 200
    if status == 206:
        out_headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    if request.method == "HEAD":
        await first.aclose()
        return Response(status_code=status, headers=out_headers, media_type=media_type)

    async def body() -> AsyncIterator[bytes]:
        resp = first
        pos = start
        try:
            while True:
                async for chunk in resp.aiter_bytes():
                    pos += len(chunk)
                    yield chunk
                await resp.aclose()
                if pos > end:
                    return
                nxt_end = min(pos + UPSTREAM_CHUNK - 1, end)
                resp = await _open(client, url, headers, pos, nxt_end)
        finally:
            await resp.aclose()

    return StreamingResponse(body(), status_code=status, headers=out_headers, media_type=media_type)
