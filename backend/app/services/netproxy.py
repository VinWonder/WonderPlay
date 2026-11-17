"""Outbound proxy for YouTube traffic (Cloudflare WARP on hosted servers).

The container's start script may launch a local WARP SOCKS proxy and set
YTDLP_PROXY. Before using it we confirm with Cloudflare's trace endpoint that
traffic really leaves through WARP; if not, everything runs direct so the app
never breaks because of the proxy.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from ..config import get_settings

log = logging.getLogger("wonderplay.netproxy")

proxy_url: str | None = None
status: dict = {"mode": "direct", "warp": None, "ip": None, "colo": None, "error": None}

TRACE = "https://www.cloudflare.com/cdn-cgi/trace"


def _parse_trace(text: str) -> dict:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


async def init(attempts: int = 8) -> None:
    global proxy_url
    url = get_settings().ytdlp_proxy.strip()
    if not url:
        return
    last = None
    for i in range(attempts):            # wireproxy needs a moment to complete the WireGuard handshake
        try:
            async with httpx.AsyncClient(proxy=url, timeout=10) as c:
                r = await c.get(TRACE)
            info = _parse_trace(r.text)
            if r.status_code == 200 and info.get("warp") in ("on", "plus"):
                proxy_url = url
                status.update(mode="warp", warp=info.get("warp"), ip=info.get("ip"), colo=info.get("colo"), error=None)
                log.info("YouTube traffic goes through Cloudflare WARP (exit %s, %s)", info.get("ip"), info.get("colo"))
                return
            last = f"proxy answered but WARP is {info.get('warp')!r}"
        except Exception as err:  # noqa: BLE001
            last = f"{type(err).__name__}: {err}"
        await asyncio.sleep(1.5 + i)
    status.update(mode="direct", error=last)
    log.warning("Cloudflare WARP proxy unavailable (%s); YouTube traffic goes direct", last)
