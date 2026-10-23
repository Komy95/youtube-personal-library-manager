"""Settings and path resolution for the YouTube manager."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pydantic import BaseModel, Field


def _default_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "youtube-personal-library-manager"
        return Path.home() / "AppData" / "Local" / "youtube-personal-library-manager"
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / "youtube-personal-library-manager"
    return Path.home() / ".local" / "share" / "youtube-personal-library-manager"


class Settings(BaseModel):
    """Application settings. No directories are created at import time."""

    data_dir: Path = Field(default_factory=_default_data_dir)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "inventory.sqlite3"

    @property
    def token_path(self) -> Path:
        return self.data_dir / "token.json"


def load_settings() -> Settings:
    """Build Settings from the environment.

    The ``YOUTUBE_MANAGER_DATA_DIR`` environment variable overrides the
    platform default data directory.
    """
    data_dir = os.environ.get("YOUTUBE_MANAGER_DATA_DIR")
    if data_dir:
        return Settings(data_dir=Path(data_dir))
    return Settings()
