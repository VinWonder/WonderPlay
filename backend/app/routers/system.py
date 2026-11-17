"""Server settings managed from the app (currently: YouTube cookies)."""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import resolver, ytcookies

router = APIRouter(prefix="/api/settings", tags=["settings"])

TEST_VIDEO = "https://www.youtube.com/watch?v=J7p4bzqLvCw"


class CookiesBody(BaseModel):
    cookies: str = Field(min_length=20, max_length=200_000)


def _status() -> dict:
    return {**ytcookies.state, "active": bool(ytcookies.current_path())}


@router.get("/youtube")
async def youtube_status() -> dict:
    return _status()


@router.put("/youtube")
async def save_youtube_cookies(body: CookiesBody) -> dict:
    try:
        await ytcookies.save(body.cookies)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    resolver.reset_pool()
    return _status()


@router.delete("/youtube")
async def remove_youtube_cookies() -> dict:
    await ytcookies.clear()
    resolver.reset_pool()
    return _status()


@router.post("/youtube/test")
async def test_youtube() -> dict:
    """Try to fetch a real song's stream from YouTube with the current setup."""
    t = time.time()
    try:
        info = await resolver.extract(TEST_VIDEO)
        ok = bool(resolver.pick_audio(info.get("formats") or [info]))
        result = {"ok": ok, "message": "YouTube playback works." if ok else "YouTube answered but sent no playable audio.",
                  "seconds": round(time.time() - t, 1)}
    except resolver.BotCheckError:
        result = {"ok": False, "seconds": round(time.time() - t, 1),
                  "message": "YouTube still asks this server to sign in. Export fresh cookies (see the steps) and save them again."}
    except resolver.ResolveError as err:
        result = {"ok": False, "seconds": round(time.time() - t, 1), "message": str(err)[:300]}
    ytcookies.state["last_test"] = {**result, "at": time.strftime("%Y-%m-%d %H:%M")}
    return result
