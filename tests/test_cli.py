"""Tests for the command-line interface."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from youtube_manager.auth import AuthError
from youtube_manager.cli import app
from youtube_manager.db import Database
from youtube_manager.models import Playlist, PlaylistItem, Snapshot, Video

runner = CliRunner()


def seed_snapshot(account_id: str = "UC1") -> Snapshot:
    return Snapshot(
        account_id=account_id,
        playlists=[Playlist(id="PL1", title="My List", item_count=2)],
        videos=[
            Video(id="v1", title="Video One", duration="PT1M"),
            Video(id="v2", title="Video Two", duration="PT2M"),
        ],
        items=[
            PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0),
            PlaylistItem(id="i2", playlist_id="PL1", video_id="v2", position=1),
        ],
    )


def seed_db(data_dir: Path, times: int = 1) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    with Database(data_dir / "inventory.sqlite3") as db:
        for _ in range(times):
            db.apply_snapshot(seed_snapshot())


class FakeClient:
    def __init__(self, account=None):
        self._account = account or {"id": "UC1", "title": "Test Channel"}

    def account(self):
        return self._account

    def list_playlists(self):
        return [Playlist(id="PL1", title="My List", item_count=2)]

    def list_playlist_items(self, playlist_id):
        return [
            PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0),
            PlaylistItem(id="i2", playlist_id="PL1", video_id="v2", position=1),
        ]

    def list_videos(self, video_ids):
        return [Video(id=vid, title=f"Video {vid}") for vid in video_ids]


def invoke(args):
    return runner.invoke(app, args)


def test_import_creates_no_files(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_MANAGER_DATA_DIR", str(tmp_path))
    assert list(tmp_path.iterdir()) == []
    assert not (tmp_path / "inventory.sqlite3").exists()
    assert not (tmp_path / "token.json").exists()


def test_help_exits_zero_no_io(tmp_path):
    result = invoke(["--data-dir", str(tmp_path), "--help"])
    assert result.exit_code == 0
    assert list(tmp_path.iterdir()) == []


def test_auth_success(monkeypatch, tmp_path):
    calls = {}

    def fake_authenticate(settings, client_secrets):
        calls["settings"] = settings
        calls["client_secrets"] = client_secrets
        return object()

    monkeypatch.setattr("youtube_manager.cli.auth_module.authenticate", fake_authenticate)
    monkeypatch.setattr("youtube_manager.cli.make_client", lambda creds: FakeClient())

    secrets = tmp_path / "client.json"
    secrets.write_text("{}", encoding="utf-8")
    data_dir = tmp_path / "data"

    result = invoke(
        ["--data-dir", str(data_dir), "auth", "--client-secrets", str(secrets)]
    )

    assert result.exit_code == 0
    assert calls["client_secrets"] == secrets
    data = json.loads(result.output)
    assert data["id"] == "UC1"
    assert data["title"] == "Test Channel"


def test_auth_uses_env_client_secrets(monkeypatch, tmp_path):
    calls = {}

    def fake_authenticate(settings, client_secrets):
        calls["client_secrets"] = client_secrets
        return object()

    monkeypatch.setattr("youtube_manager.cli.auth_module.authenticate", fake_authenticate)
    monkeypatch.setattr("youtube_manager.cli.make_client", lambda creds: FakeClient())
    monkeypatch.setenv("YOUTUBE_MANAGER_CLIENT_SECRETS", str(tmp_path / "client.json"))

    result = invoke(["--data-dir", str(tmp_path / "data"), "auth"])

    assert result.exit_code == 0
    assert calls["client_secrets"] == tmp_path / "client.json"


def test_auth_missing_client_secrets_fails(monkeypatch, tmp_path):
    monkeypatch.delenv("YOUTUBE_MANAGER_CLIENT_SECRETS", raising=False)
    result = invoke(["--data-dir", str(tmp_path / "data"), "auth"])
    assert result.exit_code != 0
    assert "client secrets" in result.output.lower()


def test_auth_api_error_shows_sanitized(monkeypatch, tmp_path):
    from youtube_manager.api import ApiError

    monkeypatch.setattr("youtube_manager.cli.auth_module.authenticate", lambda s, p: object())

    class FailingClient:
        def account(self):
            raise ApiError("YouTube API request failed.", status=403, reason="authError")

    monkeypatch.setattr("youtube_manager.cli.make_client", lambda creds: FailingClient())
    secrets = tmp_path / "client.json"
    secrets.write_text("{}", encoding="utf-8")

    result = invoke(
        ["--data-dir", str(tmp_path / "data"), "auth", "--client-secrets", str(secrets)]
    )
    assert result.exit_code != 0
    assert "API request failed" in result.output


def test_status_success(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)
    monkeypatch.setattr("youtube_manager.cli.auth_module.load_credentials", lambda s: object())
    monkeypatch.setattr("youtube_manager.cli.make_client", lambda creds: FakeClient())

    result = invoke(["--data-dir", str(data_dir), "status"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["account"]["id"] == "UC1"
    assert data["database"]["playlists"]["total"] == 1
    assert data["database"]["videos"]["total"] == 2


def test_status_missing_token_fails_helpfully(monkeypatch, tmp_path):
    def boom(settings):
        raise AuthError("No stored credentials found. Run authentication first.")

    monkeypatch.setattr("youtube_manager.cli.auth_module.load_credentials", boom)

    result = invoke(["--data-dir", str(tmp_path / "data"), "status"])

    assert result.exit_code != 0
    assert "run authentication first" in result.output.lower()


def test_playlists_no_auth(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)

    def fail(*args, **kwargs):
        raise AssertionError("credentials were accessed")

    monkeypatch.setattr("youtube_manager.cli.auth_module.load_credentials", fail)

    result = invoke(["--data-dir", str(data_dir), "playlists"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["id"] == "PL1"


def test_videos_no_auth(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)

    def fail(*args, **kwargs):
        raise AssertionError("credentials were accessed")

    monkeypatch.setattr("youtube_manager.cli.auth_module.load_credentials", fail)

    result = invoke(
        ["--data-dir", str(data_dir), "videos", "--playlist-id", "PL1"]
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 2
    assert {item["video_id"] for item in data} == {"v1", "v2"}


def test_videos_include_removed(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)
    with Database(data_dir / "inventory.sqlite3") as db:
        db.apply_snapshot(
            Snapshot(
                account_id="UC1",
                playlists=[Playlist(id="PL1", title="My List", item_count=1)],
                videos=[Video(id="v1", title="Video One")],
                items=[PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0)],
            )
        )

    def fail(*args, **kwargs):
        raise AssertionError("credentials were accessed")

    monkeypatch.setattr("youtube_manager.cli.auth_module.load_credentials", fail)

    result = invoke(
        [
            "--data-dir",
            str(data_dir),
            "videos",
            "--playlist-id",
            "PL1",
            "--include-removed",
        ]
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 2
    assert any(item["item_id"] == "i2" and item["removed_at"] for item in data)


def test_export_json_parse_and_history(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir, times=2)

    def fail(*args, **kwargs):
        raise AssertionError("credentials were accessed")

    monkeypatch.setattr("youtube_manager.cli.auth_module.load_credentials", fail)

    result = invoke(["--data-dir", str(data_dir), "export", "--format", "json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert set(data) == {"playlists", "videos", "items", "runs"}
    assert len(data["playlists"]) == 1
    assert len(data["videos"]) == 2
    assert len(data["runs"]) == 2


def test_export_to_file(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)
    out = tmp_path / "export.json"

    def fail(*args, **kwargs):
        raise AssertionError("credentials were accessed")

    monkeypatch.setattr("youtube_manager.cli.auth_module.load_credentials", fail)

    result = invoke(
        ["--data-dir", str(data_dir), "export", "--format", "json", "--output", str(out)]
    )

    assert result.exit_code == 0
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data["playlists"]) == 1


def test_export_unsupported_format(tmp_path):
    result = invoke(["--data-dir", str(tmp_path / "data"), "export", "--format", "yaml"])
    assert result.exit_code != 0
    assert "unsupported" in result.output.lower()


def test_sync_fake_integration(monkeypatch, tmp_path):
    monkeypatch.setattr("youtube_manager.cli.auth_module.load_credentials", lambda s: object())
    monkeypatch.setattr("youtube_manager.cli.make_client", lambda creds: FakeClient())

    data_dir = tmp_path / "data"
    result = invoke(["--data-dir", str(data_dir), "sync"])

    assert result.exit_code == 0
    json.loads(result.output)
    with Database(data_dir / "inventory.sqlite3") as db:
        assert len(db.list_playlists()) == 1
        assert len(db.list_videos("PL1")) == 2
