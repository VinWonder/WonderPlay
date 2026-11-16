import time

import httpx
import pytest
from fastapi import FastAPI

from app import auth
from app.config import Settings


@pytest.fixture
def settings(monkeypatch):
    s = Settings(database_url="postgres://u:p@dpg-abc123-a/db", app_password="hunter2", secret_key="k")
    monkeypatch.setattr(auth, "get_settings", lambda: s)
    return s


def test_database_url_normalised_for_render():
    s = Settings(database_url="postgres://u:p@dpg-abc123-a.oregon-postgres.render.com/db?sslmode=require")
    assert s.database_url == "postgresql+asyncpg://u:p@dpg-abc123-a.oregon-postgres.render.com/db"
    assert s.db_ssl_enabled                                       # external Render host -> SSL
    assert not Settings(database_url="postgresql://u:p@dpg-abc123-a/db").db_ssl_enabled   # internal host
    assert not Settings(database_url="postgresql+asyncpg://u:p@localhost:5432/db").db_ssl_enabled


def test_tokens(settings):
    tok = auth.make_token()
    assert auth.valid_token(tok)
    assert not auth.valid_token(tok + "x")
    assert not auth.valid_token("garbage")
    old = auth._sign(f"{int(time.time()) - auth.MAX_AGE - 10}:abc")
    assert not auth.valid_token(old)                              # expired


@pytest.fixture
def client(settings):
    app = FastAPI()
    app.add_middleware(auth.AuthMiddleware)
    app.include_router(auth.router)

    @app.get("/api/tracks")
    async def tracks():
        return {"ok": True}

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://t")


async def test_api_requires_login(client):
    assert (await client.get("/api/tracks")).status_code == 401
    assert (await client.get("/api/health")).status_code == 200
    st = (await client.get("/api/auth/status")).json()
    assert st == {"required": True, "authenticated": False}

    assert (await client.post("/api/auth/login", json={"password": "nope"})).status_code == 401
    r = await client.post("/api/auth/login", json={"password": "hunter2"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    assert (await client.get("/api/tracks")).status_code == 200
    assert (await client.get("/api/auth/status")).json()["authenticated"] is True


async def test_login_rate_limited(client):
    for _ in range(8):
        await client.post("/api/auth/login", json={"password": "wrong"}, headers={"x-forwarded-for": "9.9.9.9"})
    r = await client.post("/api/auth/login", json={"password": "hunter2"}, headers={"x-forwarded-for": "9.9.9.9"})
    assert r.status_code == 429


async def test_disabled_without_password(monkeypatch):
    s = Settings(database_url="postgres://u:p@localhost/db")
    monkeypatch.setattr(auth, "get_settings", lambda: s)
    app = FastAPI()
    app.add_middleware(auth.AuthMiddleware)

    @app.get("/api/tracks")
    async def tracks():
        return {"ok": True}

    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    assert (await c.get("/api/tracks")).status_code == 200
