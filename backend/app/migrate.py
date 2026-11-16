"""Apply db/*.sql at startup (used on hosts like Render where you can't run psql).

Files run in name order, each once; applied names are recorded in
music.schema_migrations. 00_bootstrap.sql (role/database creation) and
99_reset.sql are skipped: on a managed host the database already exists.
"""
from __future__ import annotations

import logging
import re

import asyncpg

from .config import PROJECT_DIR, get_settings

log = logging.getLogger("wonderplay.migrate")
DB_DIR = PROJECT_DIR / "db"
SKIP = {"00_bootstrap.sql", "99_reset.sql"}


def _plain_sql(text: str) -> str:
    """Strip psql meta-commands (\\set, \\connect, ...) the server can't run."""
    return "\n".join(line for line in text.splitlines() if not re.match(r"^\s*\\", line))


async def migrate() -> None:
    s = get_settings()
    dsn = s.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn, ssl="require" if s.db_ssl_enabled else None,
                                 statement_cache_size=0 if s.db_pooled else 100)
    try:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS music")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS music.schema_migrations (
                name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())
        """)
        applied = {r["name"] for r in await conn.fetch("SELECT name FROM music.schema_migrations")}

        # A database set up by hand before this runner existed: record what's there.
        if not applied and await conn.fetchval("SELECT to_regclass('music.tracks') IS NOT NULL"):
            existing = ["01_schema.sql"]
            has_lib = await conn.fetchval("""
                SELECT EXISTS (SELECT 1 FROM information_schema.columns
                                WHERE table_schema = 'music' AND table_name = 'tracks' AND column_name = 'in_library')
            """)
            if has_lib:
                existing.append("02_catalog.sql")
            await conn.executemany("INSERT INTO music.schema_migrations (name) VALUES ($1) ON CONFLICT DO NOTHING",
                                   [(n,) for n in existing])
            applied.update(existing)

        for path in sorted(DB_DIR.glob("*.sql")):
            if path.name in SKIP or path.name in applied:
                continue
            log.info("applying %s", path.name)
            sql = _plain_sql(path.read_text(encoding="utf-8"))
            await conn.execute(sql)    # each file manages its own BEGIN/COMMIT
            await conn.execute("INSERT INTO music.schema_migrations (name) VALUES ($1)", path.name)
        # Make the role resolve unqualified names in "music" for tools like psql.
        # Best effort: the app already sets search_path on every connection.
        try:
            user = await conn.fetchval("SELECT current_user")
            db = await conn.fetchval("SELECT current_database()")
            await conn.execute(f'ALTER ROLE "{user}" IN DATABASE "{db}" SET search_path = music, public')
        except asyncpg.PostgresError as err:
            log.info("could not set the role's default search_path (%s); continuing", err)
    finally:
        await conn.close()
    log.info("database schema is up to date")
