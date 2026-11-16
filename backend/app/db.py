"""Async database access.

We use SQLAlchemy Core with hand-written SQL (`text()`) instead of ORM models:
the schema already has views, triggers and functions that do the heavy lifting,
and plain SQL keeps every query visible and tunable.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from .config import get_settings

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            get_settings().database_url,
            pool_size=10,
            max_overflow=10,
            pool_pre_ping=True,
            # Belt and braces: the role already defaults to this search_path.
            connect_args={
                "server_settings": {"search_path": "music, public"},
                **({"ssl": "require"} if get_settings().db_ssl_enabled else {}),
            },
        )
    return _engine


async def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


async def get_conn() -> AsyncIterator[AsyncConnection]:
    """FastAPI dependency: one transaction per request, committed on success."""
    async with get_engine().begin() as conn:
        yield conn


# --- small helpers -------------------------------------------------------------

async def fetch_all(conn: AsyncConnection, sql: str, **params: Any) -> list[dict]:
    result = await conn.execute(text(sql), params)
    return [dict(r) for r in result.mappings().all()]


async def fetch_one(conn: AsyncConnection, sql: str, **params: Any) -> dict | None:
    result = await conn.execute(text(sql), params)
    row = result.mappings().first()
    return dict(row) if row else None


async def fetch_val(conn: AsyncConnection, sql: str, **params: Any) -> Any:
    result = await conn.execute(text(sql), params)
    return result.scalar()


async def execute(conn: AsyncConnection, sql: str, **params: Any) -> int:
    result = await conn.execute(text(sql), params)
    return result.rowcount
