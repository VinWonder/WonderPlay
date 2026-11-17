"""Server settings managed from the app (currently: YouTube cookies)."""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import netproxy, resolver, ytcookies

router = APIRouter(prefix="/api/settings", tags=["settings"])

TEST_VIDEO = "https://www.youtube.com/watch?v=J7p4bzqLvCw"


class CookiesBody(BaseModel):
    cookies: str = Field(min_length=20, max_length=200_000)


def _status() -> dict:
    return {**ytcookies.state, "active": bool(ytcookies.current_path()), "network": dict(netproxy.status)}


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
        info = await resolver.extract(TEST_VIDEO, budget=240)
        ok = bool(resolver.pick_audio(info.get("formats") or [info]))
        result = {"ok": ok, "seconds": round(time.time() - t, 1),
                  "message": f"YouTube playback works ({resolver.route_name(resolver.last_route)})." if ok
                  else "YouTube answered but sent no playable audio."}
    except resolver.BotCheckError as err:
        msg = str(err) if ytcookies.state.get("expired") else (
            "YouTube asks this server to sign in. Add backup cookies (Advanced below).")
        result = {"ok": False, "seconds": round(time.time() - t, 1), "message": msg}
    except resolver.ResolveError as err:
        result = {"ok": False, "seconds": round(time.time() - t, 1), "message": str(err)[:300]}
    result["attempts"] = resolver.last_attempts
    if not result["ok"]:
        result["diagnosis"] = await resolver.diagnose(TEST_VIDEO)
    ytcookies.state["last_test"] = {**result, "at": time.strftime("%Y-%m-%d %H:%M")}
    return result
