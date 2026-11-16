"""Request bodies. Responses are plain dicts straight from the SQL views."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

MediaKind = Literal["audio", "video"]
PlayContext = Literal["library", "album", "artist", "playlist", "search", "queue", "radio"]


class FolderCreate(BaseModel):
    path: str = Field(min_length=1)
    recursive: bool = True


class FolderUpdate(BaseModel):
    is_enabled: bool | None = None
    recursive: bool | None = None


class ScanRequest(BaseModel):
    folder_ids: list[int] | None = None
    full: bool = False  # True = re-read tags even for unchanged files


class TrackUpdate(BaseModel):
    is_favorite: bool | None = None
    rating: int | None = Field(default=None, ge=0, le=5)
    title: str | None = Field(default=None, min_length=1)
    genre: str | None = None
    explicit: bool | None = None


class SourceUpdate(BaseModel):
    sync_offset_ms: int | None = Field(default=None, ge=-600_000, le=600_000)
    is_preferred: bool | None = None


class LyricsUpdate(BaseModel):
    lrc: str | None = None          # raw LRC text (synced)
    plain_text: str | None = None
    offset_ms: int | None = Field(default=None, ge=-60_000, le=60_000)
    language: str | None = None


class RemoteImport(BaseModel):
    url: str = Field(min_length=3)
    kinds: list[MediaKind] = Field(default_factory=lambda: ["audio", "video"], min_length=1)
    track_id: int | None = None
    fetch_lyrics: bool = True

    @field_validator("url")
    @classmethod
    def _bare_id(cls, v: str) -> str:
        v = v.strip()
        # Allow pasting a bare 11-char YouTube id.
        if len(v) == 11 and "/" not in v and "." not in v:
            return f"https://www.youtube.com/watch?v={v}"
        if not v.startswith(("http://", "https://")):
            raise ValueError("url must be an http(s) URL or a YouTube video id")
        return v


class PlaylistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    track_ids: list[int] = Field(default_factory=list)


class PlaylistUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    is_pinned: bool | None = None
    keep_offline: bool | None = None


class PlaylistAdd(BaseModel):
    track_ids: list[int] = Field(min_length=1)
    position: int | None = Field(default=None, ge=0)  # None = append


class PlaylistMove(BaseModel):
    position: int = Field(ge=0)


class HistoryEntry(BaseModel):
    track_id: int
    source_id: int | None = None
    mode: MediaKind = "audio"
    context_type: PlayContext = "library"
    context_id: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    played_ms: int = Field(default=0, ge=0)
    completed: bool = False
    skipped: bool = False
    was_offline: bool = False
    device: str | None = Field(default=None, max_length=200)


class PreferencesUpdate(BaseModel):
    volume: float | None = Field(default=None, ge=0, le=1)
    muted: bool | None = None
    shuffle: bool | None = None
    repeat: Literal["off", "all", "one"] | None = None
    preferred_mode: MediaKind | None = None
    audio_quality: Literal["best", "high", "normal", "low"] | None = None
    video_max_height: Literal[360, 480, 720, 1080, 1440, 2160] | None = None
    crossfade_ms: int | None = Field(default=None, ge=0, le=12000)
    gapless: bool | None = None
    normalize_volume: bool | None = None
    visualizer_style: Literal["off", "bars", "wave", "circle"] | None = None
    dynamic_theme: bool | None = None
    show_synced_lyrics: bool | None = None
    last_track_id: int | None = None
    last_position_ms: int | None = Field(default=None, ge=0)
    queue_track_ids: list[int] | None = None
    queue_index: int | None = Field(default=None, ge=0)
    extra: dict | None = None
