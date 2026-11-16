"""Single-user password protection for public hosting.

Enabled only when APP_PASSWORD is set (local use stays password-free).
A successful login sets an HttpOnly, signed session cookie; every /api/*
request except health/auth must carry it. The static app shell stays public
(it contains no data) so the login screen and the PWA can load.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import get_settings

COOKIE = "wp_session"
MAX_AGE = 180 * 24 * 3600          # stay signed in for ~6 months
PUBLIC_API = ("/api/health", "/api/auth/")

router = APIRouter(prefix="/api/auth", tags=["auth"])
_failures: dict[str, deque] = defaultdict(deque)


def enabled() -> bool:
    return bool(get_settings().app_password)


def _key() -> bytes:
    s = get_settings()
    # Signing key: SECRET_KEY if given, otherwise derived from the password
    # (changing the password then logs every device out, which is desirable).
    return hashlib.sha256((s.secret_key or f"wonderplay:{s.app_password}").encode()).digest()


def _sign(payload: str) -> str:
    mac = hmac.new(_key(), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{base64.urlsafe_b64encode(mac).decode().rstrip('=')}"


def make_token() -> str:
    return _sign(f"{int(time.time())}:{secrets.token_urlsafe(8)}")


def valid_token(token: str | None) -> bool:
    if not token or token.count(".") != 1:
        return False
    payload, _ = token.split(".", 1)
    if not hmac.compare_digest(_sign(payload), token):
        return False
    try:
        issued = int(payload.split(":", 1)[0])
    except ValueError:
        return False
    return time.time() - issued < MAX_AGE


def is_authenticated(request: Request) -> bool:
    return not enabled() or valid_token(request.cookies.get(COOKIE))


class AuthMiddleware:
    """Plain ASGI middleware (not BaseHTTPMiddleware) so long audio/video
    streams pass through untouched."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and enabled():
            path = scope.get("path", "")
            if path.startswith("/api/") and not path.startswith(PUBLIC_API):
                if not valid_token(Request(scope).cookies.get(COOKIE)):
                    await JSONResponse({"detail": "Sign in required"}, status_code=401)(scope, receive, send)
                    return
        await self.app(scope, receive, send)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=500)


@router.get("/status")
async def status(request: Request) -> dict:
    return {"required": enabled(), "authenticated": is_authenticated(request)}


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    if not enabled():
        return {"ok": True}
    ip = _client_ip(request)
    window = _failures[ip]
    now = time.time()
    while window and now - window[0] > 600:
        window.popleft()
    if len(window) >= 8:   # 8 wrong passwords per 10 minutes per address
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in a few minutes.")
    if not hmac.compare_digest(body.password.encode(), get_settings().app_password.encode()):
        window.append(now)
        raise HTTPException(status_code=401, detail="Wrong password")
    window.clear()
    response.set_cookie(COOKIE, make_token(), max_age=MAX_AGE, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/")
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}
