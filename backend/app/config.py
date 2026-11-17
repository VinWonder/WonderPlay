"""Application settings, loaded from the project-root .env file."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    media_roots: str = ""
    cover_cache_dir: Path = Path("./data/covers")
    download_dir: Path = Path("./data/downloads")

    ytdlp_stream_ttl_seconds: int = 18000
    ffmpeg_location: str = ""
    # JS runtime yt-dlp uses to solve YouTube challenges ("node" or "deno").
    ytdlp_js_runtime: str = "node"
    # YouTube clients tried first (fast path). On failure yt-dlp's defaults are used.
    ytdlp_player_clients: str = "tv_samsung"
    # "actual" = use the player JS the page really serves. yt-dlp otherwise forces
    # the "main" variant, which some networks throttle to a few KB/s.
    ytdlp_player_js_variant: str = "actual"
    # Optional Netscape cookies.txt for age-restricted / members content.
    ytdlp_cookies_file: str = ""
    # Outbound proxy for YouTube (set by docker/start.sh when WARP_PROXY=true),
    # e.g. socks5://127.0.0.1:40000. Verified at startup before it is used.
    ytdlp_proxy: str = ""

    # Run an incremental library scan each time the server starts.
    scan_on_startup: bool = True
    # Create / upgrade the database schema at startup (db/*.sql). Used on Render.
    auto_migrate: bool = False
    # "auto" = SSL for remote hosts (e.g. Render's external URL), off for localhost
    # and Render's internal hostnames. Or force "true" / "false".
    db_ssl: str = "auto"

    # Hosting: set APP_PASSWORD to require a login (single user).
    app_password: str = ""
    secret_key: str = ""

    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"

    @field_validator("cover_cache_dir", "download_dir")
    @classmethod
    def _anchor_to_project(cls, v: Path) -> Path:
        # Relative paths in .env are relative to the project root, not the CWD.
        return v if v.is_absolute() else (PROJECT_DIR / v).resolve()

    @field_validator("database_url")
    @classmethod
    def _asyncpg_url(cls, v: str) -> str:
        # Render (and Heroku-style hosts) hand out postgres:// or postgresql:// URLs.
        v = v.strip()
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                v = "postgresql+asyncpg://" + v[len(prefix):]
        # Hosted Postgres URLs (Neon, Render, Supabase...) carry libpq-only options
        # such as ?sslmode=require&channel_binding=require. asyncpg rejects unknown
        # options, so drop them all; SSL is handled via db_ssl instead.
        if "?" in v:
            v = v.split("?", 1)[0]
        return v

    @property
    def db_host(self) -> str:
        return self.database_url.split("@")[-1].split("/")[0].split(":")[0]

    @property
    def db_pooled(self) -> bool:
        """Behind a transaction-mode pooler (PgBouncer), e.g. Neon's '-pooler'
        endpoints: asyncpg's prepared-statement cache must be disabled."""
        return "-pooler" in self.db_host or "pgbouncer" in self.db_host

    @property
    def db_ssl_enabled(self) -> bool:
        mode = self.db_ssl.strip().lower()
        if mode in ("true", "1", "yes", "require"):
            return True
        if mode in ("false", "0", "no", "disable"):
            return False
        host = self.db_host
        # Render internal hostnames look like "dpg-xxxx-a" (no dots); external ones are FQDNs.
        return "." in host and host not in ("localhost", "127.0.0.1") and not host.endswith(".internal")

    @property
    def media_root_list(self) -> list[str]:
        return [p.strip() for p in self.media_roots.split(";") if p.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def frontend_dir(self) -> Path:
        return PROJECT_DIR / "frontend"


@lru_cache
def get_settings() -> Settings:
    return Settings()
