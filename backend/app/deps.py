"""Shared FastAPI dependencies."""
from __future__ import annotations

import httpx
from fastapi import HTTPException, Request


def get_http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http


def get_media_http(request: Request) -> httpx.AsyncClient:
    """Client for YouTube media: goes through WARP when it is active, because
    stream URLs only work from the address that resolved them."""
    return getattr(request.app.state, "http_media", request.app.state.http)


def not_found(what: str = "resource") -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} not found")


def paging(limit: int = 50, offset: int = 0) -> dict:
    return {"limit": max(1, min(limit, 500)), "offset": max(0, offset)}
