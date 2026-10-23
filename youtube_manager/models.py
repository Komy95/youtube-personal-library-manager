"""Pydantic data models for the YouTube inventory."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Availability(str, Enum):
    available = "available"
    private = "private"
    deleted = "deleted"
    unavailable = "unavailable"


class Playlist(BaseModel):
    id: str
    title: str = ""
    description: str = ""
    channel_id: str = ""
    channel_title: str = ""
    published_at: str | None = None
    privacy_status: str = "unavailable"
    item_count: int = 0


class Video(BaseModel):
    id: str
    title: str = ""
    description: str = ""
    channel_id: str = ""
    channel_title: str = ""
    duration: str | None = None
    privacy_status: str | None = None
    availability: Availability = Availability.available
    published_at: str | None = None


class PlaylistItem(BaseModel):
    id: str
    playlist_id: str
    video_id: str
    position: int = 0
    title: str = ""
    availability: Availability = Availability.available


class Snapshot(BaseModel):
    account_id: str
    playlists: list[Playlist] = Field(default_factory=list)
    videos: list[Video] = Field(default_factory=list)
    items: list[PlaylistItem] = Field(default_factory=list)
    captured_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
