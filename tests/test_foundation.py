"""Foundation tests: path isolation, no import side effects, model JSON."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

import youtube_manager
from youtube_manager.config import Settings, load_settings
from youtube_manager.models import Availability, Playlist, PlaylistItem, Snapshot, Video


def test_import_has_no_side_effects(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_MANAGER_DATA_DIR", str(tmp_path))
    assert not (tmp_path / "inventory.sqlite3").exists()
    assert not (tmp_path / "token.json").exists()
    assert youtube_manager.__version__ == "0.1.0"


def test_settings_default_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", "C:/Users/test/AppData/Local")
    monkeypatch.delenv("YOUTUBE_MANAGER_DATA_DIR", raising=False)
    s = Settings()
    assert s.data_dir == Path("C:/Users/test/AppData/Local") / "youtube-personal-library-manager"


def test_settings_default_xdg(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/tmp/xdg")
    monkeypatch.delenv("YOUTUBE_MANAGER_DATA_DIR", raising=False)
    s = Settings()
    assert s.data_dir == Path("/tmp/xdg") / "youtube-personal-library-manager"


def test_settings_home_fallback(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("YOUTUBE_MANAGER_DATA_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: Path("/home/tester"))
    s = Settings()
    assert s.data_dir == Path("/home/tester") / ".local" / "share" / "youtube-personal-library-manager"


def test_settings_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("YOUTUBE_MANAGER_DATA_DIR", str(tmp_path / "custom"))
    s = load_settings()
    assert s.data_dir == tmp_path / "custom"


def test_settings_properties(tmp_path):
    s = Settings(data_dir=tmp_path)
    assert s.db_path == tmp_path / "inventory.sqlite3"
    assert s.token_path == tmp_path / "token.json"
    assert not s.db_path.exists()


def test_model_json_serialization():
    playlist = Playlist(id="PL123", title="My List", item_count=3)
    video = Video(id="vid1", availability=Availability.private)
    item = PlaylistItem(id="item1", playlist_id="PL123", video_id="vid1")
    snapshot = Snapshot(
        account_id="acct1",
        playlists=[playlist],
        videos=[video],
        items=[item],
    )
    data = json.loads(snapshot.model_dump_json())
    assert data["account_id"] == "acct1"
    assert data["playlists"][0]["id"] == "PL123"
    assert data["videos"][0]["availability"] == "private"
    assert data["items"][0]["position"] == 0
    assert "captured_at" in data


def test_model_defaults():
    v = Video(id="x")
    assert v.title == ""
    assert v.availability == Availability.available
    assert Playlist(id="p").privacy_status == "unavailable"
