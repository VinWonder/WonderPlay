"""The remote proxy against a fake range-capable CDN."""
import os
import re

import httpx
import pytest
from fastapi import FastAPI, Request

from app.services import proxy
from app.services.proxy import UpstreamExpired, proxy_stream

BLOB = os.urandom(50_000)


def make_cdn(log: list, *, expired: bool = False):
    def handler(req: httpx.Request) -> httpx.Response:
        log.append(req.headers.get("range"))
        if expired:
            return httpx.Response(403)
        m = re.match(r"bytes=(\d+)-(\d+)", req.headers["range"])
        s, e = int(m.group(1)), min(int(m.group(2)), len(BLOB) - 1)
        return httpx.Response(206, content=BLOB[s:e + 1], headers={
            "Content-Range": f"bytes {s}-{e}/{len(BLOB)}", "Content-Type": "audio/mp4"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def make_app(cdn: httpx.AsyncClient) -> httpx.AsyncClient:
    app = FastAPI()

    @app.api_route("/s", methods=["GET", "HEAD"])
    async def s(request: Request):
        return await proxy_stream(request, cdn, "https://cdn.example/x", {"User-Agent": "UA"},
                                  media_type="audio/mp4")

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch):
    monkeypatch.setattr(proxy, "UPSTREAM_CHUNK", 16_384)


async def test_open_ended_range_is_chunked_upstream():
    log: list = []
    client = make_app(make_cdn(log))
    r = await client.get("/s", headers={"Range": "bytes=1000-"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 1000-{len(BLOB) - 1}/{len(BLOB)}"
    assert r.content == BLOB[1000:]
    # every upstream request is bounded (googlevideo throttles open-ended ones)
    assert all(re.fullmatch(r"bytes=\d+-\d+", h) for h in log) and len(log) == 3  # 49 000 bytes / 16 KiB chunks


async def test_no_range_gives_200_full_body():
    client = make_app(make_cdn([]))
    r = await client.get("/s")
    assert r.status_code == 200 and r.content == BLOB
    assert r.headers["content-length"] == str(len(BLOB))


async def test_bounded_and_suffix_ranges():
    client = make_app(make_cdn([]))
    r = await client.get("/s", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206 and r.content == BLOB[100:200]
    r = await client.get("/s", headers={"Range": "bytes=-500"})
    assert r.status_code == 206 and r.content == BLOB[-500:]


async def test_expired_url_raises_before_streaming():
    client = make_app(make_cdn([], expired=True))
    with pytest.raises(UpstreamExpired):
        await client.get("/s", headers={"Range": "bytes=0-"})
