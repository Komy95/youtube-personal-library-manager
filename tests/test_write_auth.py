"""Tests for the write-scope OAuth authentication module."""

from __future__ import annotations

import datetime
import json
import traceback
from pathlib import Path
from unittest import mock

import pytest

from google.oauth2.credentials import Credentials

import youtube_manager.write_auth as write_auth
from youtube_manager.auth import AuthError
from youtube_manager.config import Settings
from youtube_manager.db import Database
from youtube_manager.models import Playlist, PlaylistItem, Snapshot, Video
from youtube_manager.planning import build_plan, save_plan
from youtube_manager.write_auth import (
    WRITE_SCOPES,
    authenticate_write,
    load_write_credentials,
)


def _installed_config() -> dict:
    return {
        "installed": {
            "client_id": "cid",
            "client_secret": "csec",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def _write_secrets(path: Path) -> None:
    path.write_text(json.dumps(_installed_config()), encoding="utf-8")


def _write_creds(**overrides):
    data = {
        "token": "tok",
        "refresh_token": "refresh",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "cid",
        "client_secret": "csec",
        "scopes": list(WRITE_SCOPES),
    }
    data.update(overrides)
    return Credentials(**data)


def _future_expiry() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
    ).isoformat()


def _info(expired: bool = False, **overrides) -> dict:
    info = {
        "token": "tok",
        "refresh_token": "refresh",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "cid",
        "client_secret": "csec",
        "scopes": list(WRITE_SCOPES),
    }
    if expired:
        info["expiry"] = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(hours=1)
        ).isoformat()
    else:
        info["expiry"] = _future_expiry()
    info.update(overrides)
    return info


def _store_write_token(data_dir: Path, info: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "write_token.json").write_bytes(json.dumps(info).encode("utf-8"))


def make_snapshot():
    return Snapshot(
        account_id="acct1",
        playlists=[Playlist(id="PL1", title="List", item_count=2)],
        videos=[Video(id="v1", title="One"), Video(id="v2", title="Two")],
        items=[
            PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0),
            PlaylistItem(id="i2", playlist_id="PL1", video_id="v2", position=1),
        ],
        captured_at="2026-01-01T00:00:00+00:00",
    )


def make_db_with_plan(path: Path):
    db = Database(path)
    db.apply_snapshot(make_snapshot())
    plan = build_plan(db, {"Cat": ["v1"]})
    save_plan(db, plan)
    db.close()
    return plan


@pytest.fixture
def identity_cipher(monkeypatch):
    monkeypatch.setattr(write_auth, "_protect", lambda b: b)
    monkeypatch.setattr(write_auth, "_unprotect", lambda b: b)


def test_write_scopes_only_force_ssl():
    assert WRITE_SCOPES == ("https://www.googleapis.com/auth/youtube.force-ssl",)


def test_authenticate_write_pkce_loopback(tmp_path):
    settings = Settings(data_dir=tmp_path)
    plan = make_db_with_plan(settings.db_path)
    secrets = tmp_path / "client_secrets.json"
    _write_secrets(secrets)
    creds = _write_creds()

    flow = mock.MagicMock()
    flow.run_local_server.return_value = creds
    with mock.patch.object(
        write_auth.InstalledAppFlow, "from_client_config", return_value=flow
    ) as fcc:
        with mock.patch.object(write_auth, "_store_write_credentials") as store:
            result = authenticate_write(settings, secrets, plan.id)

    assert result is creds
    kwargs = fcc.call_args.kwargs
    assert kwargs["scopes"] == WRITE_SCOPES
    assert kwargs["autogenerate_code_verifier"] is True
    run = flow.run_local_server.call_args.kwargs
    assert run["host"] == "127.0.0.1"
    assert run["port"] == 0
    assert run["open_browser"] is True
    assert run["timeout_seconds"] == 180
    assert run["authorization_prompt_message"] == ""
    assert run["prompt"] == "consent"
    assert run["access_type"] == "offline"
    store.assert_called_once()


def test_authenticate_write_requires_refresh_token(tmp_path):
    settings = Settings(data_dir=tmp_path)
    plan = make_db_with_plan(settings.db_path)
    secrets = tmp_path / "client_secrets.json"
    _write_secrets(secrets)
    creds = _write_creds(refresh_token=None)

    flow = mock.MagicMock()
    flow.run_local_server.return_value = creds
    with mock.patch.object(write_auth.InstalledAppFlow, "from_client_config", return_value=flow):
        with mock.patch.object(write_auth, "_store_write_credentials"):
            with pytest.raises(AuthError, match="refresh"):
                authenticate_write(settings, secrets, plan.id)


@pytest.mark.parametrize(
    "approved",
    [
        "short",
        "g" * 64,
        "0" * 63,
        "0" * 65,
        123,
        None,
    ],
)
def test_invalid_approved_plan_no_oauth(tmp_path, approved):
    settings = Settings(data_dir=tmp_path)
    secrets = tmp_path / "client_secrets.json"
    _write_secrets(secrets)
    with mock.patch.object(write_auth.InstalledAppFlow, "from_client_config") as fcc:
        with mock.patch.object(write_auth, "_store_write_credentials") as store:
            with pytest.raises(AuthError, match="approved_plan"):
                authenticate_write(settings, secrets, approved)
    fcc.assert_not_called()
    store.assert_not_called()


def test_missing_plan_no_oauth(tmp_path):
    settings = Settings(data_dir=tmp_path)
    secrets = tmp_path / "client_secrets.json"
    _write_secrets(secrets)
    with mock.patch.object(write_auth.InstalledAppFlow, "from_client_config") as fcc:
        with mock.patch.object(write_auth, "_store_write_credentials") as store:
            with pytest.raises(AuthError, match="planning"):
                authenticate_write(settings, secrets, "a" * 64)
    fcc.assert_not_called()
    store.assert_not_called()


def test_wrong_hash_plan_no_oauth(tmp_path):
    settings = Settings(data_dir=tmp_path)
    plan = make_db_with_plan(settings.db_path)
    db = Database(settings.db_path)
    try:
        row = db._conn.execute(
            "SELECT data_json FROM migration_plans WHERE id = ?", (plan.id,)
        ).fetchone()
        data = json.loads(row["data_json"])
        data["hash"] = "f" * 64
        db._conn.execute(
            "UPDATE migration_plans SET data_json = ? WHERE id = ?",
            (json.dumps(data), plan.id),
        )
        db._conn.commit()
    finally:
        db.close()
    secrets = tmp_path / "client_secrets.json"
    _write_secrets(secrets)
    with mock.patch.object(write_auth.InstalledAppFlow, "from_client_config") as fcc:
        with mock.patch.object(write_auth, "_store_write_credentials") as store:
            with pytest.raises(AuthError, match="corrupt"):
                authenticate_write(settings, secrets, plan.id)
    fcc.assert_not_called()
    store.assert_not_called()


def test_plan_id_mismatch_no_oauth(tmp_path):
    settings = Settings(data_dir=tmp_path)
    plan = make_db_with_plan(settings.db_path)
    db = Database(settings.db_path)
    try:
        row = db._conn.execute(
            "SELECT data_json FROM migration_plans WHERE id = ?", (plan.id,)
        ).fetchone()
        data = json.loads(row["data_json"])
        data["id"] = "f" * 64
        db._conn.execute(
            "UPDATE migration_plans SET data_json = ? WHERE id = ?",
            (json.dumps(data), plan.id),
        )
        db._conn.commit()
    finally:
        db.close()
    secrets = tmp_path / "client_secrets.json"
    _write_secrets(secrets)
    with mock.patch.object(write_auth.InstalledAppFlow, "from_client_config") as fcc:
        with mock.patch.object(write_auth, "_store_write_credentials") as store:
            with pytest.raises(AuthError, match="corrupt"):
                authenticate_write(settings, secrets, plan.id)
    fcc.assert_not_called()
    store.assert_not_called()


def test_stale_plan_no_oauth(tmp_path):
    settings = Settings(data_dir=tmp_path)
    plan = make_db_with_plan(settings.db_path)
    db = Database(settings.db_path)
    try:
        db.apply_snapshot(
            Snapshot(
                account_id="acct1",
                playlists=[],
                videos=[],
                items=[],
                captured_at="2026-02-01T00:00:00+00:00",
            )
        )
    finally:
        db.close()
    secrets = tmp_path / "client_secrets.json"
    _write_secrets(secrets)
    with mock.patch.object(write_auth.InstalledAppFlow, "from_client_config") as fcc:
        with mock.patch.object(write_auth, "_store_write_credentials") as store:
            with pytest.raises(AuthError, match="inventory"):
                authenticate_write(settings, secrets, plan.id)
    fcc.assert_not_called()
    store.assert_not_called()


def test_load_write_missing_plan_no_token_read(tmp_path, identity_cipher):
    settings = Settings(data_dir=tmp_path)
    _store_write_token(tmp_path, _info())
    with mock.patch.object(write_auth, "_unprotect") as unprotect:
        with pytest.raises(AuthError, match="planning"):
            load_write_credentials(settings, "a" * 64)
    unprotect.assert_not_called()


def test_refresh_does_not_launch_browser(tmp_path, identity_cipher):
    settings = Settings(data_dir=tmp_path)
    plan = make_db_with_plan(settings.db_path)
    _store_write_token(tmp_path, _info(expired=True))
    with mock.patch.object(Credentials, "refresh") as refresh:
        with mock.patch.object(write_auth, "Request") as request:
            with mock.patch.object(write_auth, "_store_write_credentials") as store:
                with mock.patch.object(
                    write_auth.InstalledAppFlow, "from_client_config"
                ) as fcc:
                    load_write_credentials(settings, plan.id)
    refresh.assert_called_once_with(request.return_value)
    store.assert_called_once()
    fcc.assert_not_called()


def test_valid_load_no_refresh(tmp_path, identity_cipher):
    settings = Settings(data_dir=tmp_path)
    plan = make_db_with_plan(settings.db_path)
    _store_write_token(tmp_path, _info())
    with mock.patch.object(Credentials, "refresh") as refresh:
        creds = load_write_credentials(settings, plan.id)
    refresh.assert_not_called()
    assert set(creds.scopes) == set(WRITE_SCOPES)


def test_storage_encrypted_and_readonly_untouched(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    plan = make_db_with_plan(settings.db_path)
    (tmp_path / "token.json").write_bytes(b"READONLY-SENTINEL")

    key = 0x5A
    monkeypatch.setattr(write_auth, "_protect", lambda b: bytes(x ^ key for x in b))
    monkeypatch.setattr(write_auth, "_unprotect", lambda b: bytes(x ^ key for x in b))

    secrets = tmp_path / "client_secrets.json"
    _write_secrets(secrets)
    flow = mock.MagicMock()
    flow.run_local_server.return_value = _write_creds()
    with mock.patch.object(write_auth.InstalledAppFlow, "from_client_config", return_value=flow):
        authenticate_write(settings, secrets, plan.id)

    assert (tmp_path / "token.json").read_bytes() == b"READONLY-SENTINEL"
    raw = (tmp_path / "write_token.json").read_bytes()
    assert raw != _write_creds().to_json().encode("utf-8")
    decrypted = write_auth._unprotect(raw)
    assert json.loads(decrypted.decode("utf-8"))["refresh_token"] == "refresh"


@pytest.mark.parametrize(
    "overrides",
    [
        {"scopes": list(WRITE_SCOPES) + ["https://evil.example/x"]},
        {"scope": "https://www.googleapis.com/auth/youtube.force-ssl https://evil.example/x"},
        {"token_uri": "https://evil.example/token"},
    ],
)
def test_corrupt_wrong_cached_rejected(tmp_path, identity_cipher, overrides):
    settings = Settings(data_dir=tmp_path)
    plan = make_db_with_plan(settings.db_path)
    info = _info()
    if "scope" in overrides:
        del info["scopes"]
    info.update(overrides)
    _store_write_token(tmp_path, info)
    with pytest.raises(AuthError):
        load_write_credentials(settings, plan.id)


def test_missing_cached_scopes_rejected(tmp_path, identity_cipher):
    settings = Settings(data_dir=tmp_path)
    plan = make_db_with_plan(settings.db_path)
    info = _info()
    del info["scopes"]
    _store_write_token(tmp_path, info)
    with pytest.raises(AuthError, match="scope"):
        load_write_credentials(settings, plan.id)


def test_conflicting_scope_and_scopes_rejected(tmp_path, identity_cipher):
    settings = Settings(data_dir=tmp_path)
    plan = make_db_with_plan(settings.db_path)
    info = _info()
    info["scope"] = "https://www.googleapis.com/auth/youtube.force-ssl https://evil.example/x"
    _store_write_token(tmp_path, info)
    with pytest.raises(AuthError, match="scope"):
        load_write_credentials(settings, plan.id)


def test_nonstring_scope_rejected(tmp_path, identity_cipher):
    settings = Settings(data_dir=tmp_path)
    plan = make_db_with_plan(settings.db_path)
    info = _info()
    del info["scopes"]
    info["scope"] = 123
    _store_write_token(tmp_path, info)
    with pytest.raises(AuthError, match="scope"):
        load_write_credentials(settings, plan.id)


def test_missing_cached_token_uri_rejected(tmp_path, identity_cipher):
    settings = Settings(data_dir=tmp_path)
    plan = make_db_with_plan(settings.db_path)
    info = _info()
    del info["token_uri"]
    _store_write_token(tmp_path, info)
    with pytest.raises(AuthError, match="token"):
        load_write_credentials(settings, plan.id)


def test_corrupted_write_token_rejected(tmp_path, identity_cipher):
    settings = Settings(data_dir=tmp_path)
    plan = make_db_with_plan(settings.db_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "write_token.json").write_bytes(b"{not valid json")
    with pytest.raises(AuthError, match="corrupt"):
        load_write_credentials(settings, plan.id)


def test_missing_write_token_actionable(tmp_path, identity_cipher):
    settings = Settings(data_dir=tmp_path)
    plan = make_db_with_plan(settings.db_path)
    with pytest.raises(AuthError, match="authenticat"):
        load_write_credentials(settings, plan.id)


def test_raw_oauth_exception_sanitized(tmp_path):
    settings = Settings(data_dir=tmp_path)
    plan = make_db_with_plan(settings.db_path)
    secrets = tmp_path / "client_secrets.json"
    _write_secrets(secrets)
    secret = "client_secret=SUPERSECRET https://accounts.google.com/o/oauth2/auth"

    def boom(*args, **kwargs):
        raise RuntimeError(secret)

    with mock.patch.object(
        write_auth.InstalledAppFlow, "from_client_config", side_effect=boom
    ):
        with pytest.raises(AuthError) as exc:
            authenticate_write(settings, secrets, plan.id)
    message = str(exc.value)
    assert "SUPERSECRET" not in message
    assert "client_secret" not in message
    assert "accounts.google.com" not in message
    tb = "".join(
        traceback.format_exception(type(exc.value), exc.value, exc.value.__traceback__)
    )
    assert "SUPERSECRET" not in tb
    assert "accounts.google.com" not in tb


def test_failed_refresh_sanitized(tmp_path, identity_cipher):
    settings = Settings(data_dir=tmp_path)
    plan = make_db_with_plan(settings.db_path)
    _store_write_token(tmp_path, _info(expired=True))
    with mock.patch.object(
        Credentials, "refresh", side_effect=Exception("secret /home/u/client.json boom")
    ):
        with pytest.raises(AuthError) as exc:
            load_write_credentials(settings, plan.id)
    message = str(exc.value)
    assert "secret" not in message
    assert "/home/u" not in message
    assert "client.json" not in message
