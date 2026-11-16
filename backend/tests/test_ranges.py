import os

import httpx
import pytest
from fastapi import FastAPI, Request

from app.services.ranges import ByteRange, RangeNotSatisfiable, parse_range, range_file_response

SIZE = 1000


@pytest.mark.parametrize("header,expected", [
    (None, None),
    ("", None),
    ("bytes=0-99", ByteRange(0, 99)),
    ("bytes=500-", ByteRange(500, 999)),
    ("bytes=-100", ByteRange(900, 999)),
    ("bytes=-5000", ByteRange(0, 999)),          # suffix larger than file -> whole file
    ("bytes=990-5000", ByteRange(990, 999)),     # end clamped
    ("bytes=0-0", ByteRange(0, 0)),
    ("bytes=0-1,5-9", None),                      # multi-range ignored -> 200
    ("items=0-5", None),                          # unknown unit ignored
    ("bytes=9-3", None),                          # end < start: invalid, ignored
])
def test_parse_range(header, expected):
    assert parse_range(header, SIZE) == expected


@pytest.mark.parametrize("header", ["bytes=1000-", "bytes=2000-3000", "bytes=-0"])
def test_unsatisfiable(header):
    with pytest.raises(RangeNotSatisfiable):
        parse_range(header, SIZE)


@pytest.fixture
def media_file(tmp_path):
    p = tmp_path / "song.mp3"
    p.write_bytes(os.urandom(700_000))  # > CHUNK_SIZE so multi-chunk reads are exercised
    return p


@pytest.fixture
def client(media_file):
    app = FastAPI()

    @app.api_route("/f", methods=["GET", "HEAD"])
    async def f(request: Request):
        return range_file_response(request, media_file)

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_full_response(client, media_file):
    r = await client.get("/f")
    assert r.status_code == 200
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.content == media_file.read_bytes()


async def test_partial_content(client, media_file):
    data = media_file.read_bytes()
    r = await client.get("/f", headers={"Range": "bytes=300000-"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 300000-{len(data) - 1}/{len(data)}"
    assert int(r.headers["content-length"]) == len(data) - 300000
    assert r.content == data[300000:]

    r = await client.get("/f", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == data[10:20]


async def test_head_and_416(client, media_file):
    size = media_file.stat().st_size
    r = await client.head("/f", headers={"Range": "bytes=0-1"})
    assert r.status_code == 206 and r.content == b"" and r.headers["content-length"] == "2"
    r = await client.get("/f", headers={"Range": f"bytes={size}-"})
    assert r.status_code == 416 and r.headers["content-range"] == f"bytes */{size}"


async def test_conditional(client):
    etag = (await client.get("/f")).headers["etag"]
    assert (await client.get("/f", headers={"If-None-Match": etag})).status_code == 304
    # stale If-Range -> full body instead of a partial one
    r = await client.get("/f", headers={"Range": "bytes=0-9", "If-Range": '"stale"'})
    assert r.status_code == 200
    r = await client.get("/f", headers={"Range": "bytes=0-9", "If-Range": etag})
    assert r.status_code == 206
