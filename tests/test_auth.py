"""Tests for the OAuth authentication module."""

from __future__ import annotations

import datetime
import json
import os
import sys
import traceback
from pathlib import Path
from unittest import mock

import pytest

from google.oauth2.credentials import Credentials

import youtube_manager.auth as auth
from youtube_manager.auth import AuthError, SCOPES, authenticate, load_credentials
from youtube_manager.config import Settings


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


def _creds(**overrides):
    data = {
        "token": "tok",
        "refresh_token": "refresh",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "cid",
        "client_secret": "csec",
        "scopes": list(SCOPES),
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
        "scopes": list(SCOPES),
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


def _store_token(data_dir: Path, info: dict) -> None:
    token_path = data_dir / "token.json"
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_bytes(json.dumps(info).encode("utf-8"))


@pytest.fixture
def identity_cipher(monkeypatch):
    monkeypatch.setattr(auth, "_protect", lambda b: b)
    monkeypatch.setattr(auth, "_unprotect", lambda b: b)


def test_scopes_only_readonly():
    assert SCOPES == ("https://www.googleapis.com/auth/youtube.readonly",)


def test_authenticate_readonly_pkce_loopback(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / "data")
    secrets = tmp_path / "client_secrets.json"
    _write_secrets(secrets)
    creds = _creds()

    flow = mock.MagicMock()
    flow.run_local_server.return_value = creds
    with mock.patch.object(
        auth.InstalledAppFlow, "from_client_config", return_value=flow
    ) as fcc:
        with mock.patch.object(auth, "_store_credentials") as store:
            result = authenticate(settings, secrets)

    assert result is creds
    kwargs = fcc.call_args.kwargs
    assert kwargs["scopes"] == SCOPES
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


def test_authenticate_requires_refresh_token(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    secrets = tmp_path / "client_secrets.json"
    _write_secrets(secrets)
    creds = _creds(refresh_token=None)

    flow = mock.MagicMock()
    flow.run_local_server.return_value = creds
    with mock.patch.object(auth.InstalledAppFlow, "from_client_config", return_value=flow):
        with mock.patch.object(auth, "_store_credentials"):
            with pytest.raises(AuthError, match="refresh"):
                authenticate(settings, secrets)


@pytest.mark.parametrize(
    "patch",
    [
        lambda c: c["installed"].update(auth_uri="https://evil.example/auth"),
        lambda c: c["installed"].update(token_uri="https://evil.example/token"),
    ],
)
def test_tampered_client_endpoints_rejected(tmp_path, patch):
    config = _installed_config()
    patch(config)
    secrets = tmp_path / "client_secrets.json"
    secrets.write_text(json.dumps(config), encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data")
    with pytest.raises(AuthError):
        authenticate(settings, secrets)


def test_web_client_type_rejected(tmp_path):
    secrets = tmp_path / "client_secrets.json"
    secrets.write_text(
        json.dumps({"web": {"auth_uri": "https://evil.example"}}), encoding="utf-8"
    )
    settings = Settings(data_dir=tmp_path / "data")
    with pytest.raises(AuthError):
        authenticate(settings, secrets)


def test_mixed_web_and_installed_rejected(tmp_path):
    config = _installed_config()
    config["web"] = {"client_id": "wc", "client_secret": "ws"}
    secrets = tmp_path / "client_secrets.json"
    secrets.write_text(json.dumps(config), encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data")
    with pytest.raises(AuthError, match="web"):
        authenticate(settings, secrets)


def test_broad_cached_scopes_rejected(tmp_path, identity_cipher):
    data_dir = tmp_path / "data"
    _store_token(data_dir, _info(scopes=list(SCOPES) + ["https://evil.example/x"]))
    with pytest.raises(AuthError, match="scope"):
        load_credentials(Settings(data_dir=data_dir))


def test_broad_cached_scope_string_rejected(tmp_path, identity_cipher):
    data_dir = tmp_path / "data"
    info = _info()
    del info["scopes"]
    info["scope"] = "https://www.googleapis.com/auth/youtube.readonly https://evil.example/x"
    _store_token(data_dir, info)
    with pytest.raises(AuthError, match="scope"):
        load_credentials(Settings(data_dir=data_dir))


def test_cached_token_uri_tampered_rejected(tmp_path, identity_cipher):
    data_dir = tmp_path / "data"
    _store_token(data_dir, _info(token_uri="https://evil.example/token"))
    with pytest.raises(AuthError, match="token"):
        load_credentials(Settings(data_dir=data_dir))


def test_missing_cached_scopes_rejected(tmp_path, identity_cipher):
    data_dir = tmp_path / "data"
    info = _info()
    del info["scopes"]
    _store_token(data_dir, info)
    with pytest.raises(AuthError, match="scope"):
        load_credentials(Settings(data_dir=data_dir))


def test_invalid_cached_scopes_type_rejected(tmp_path, identity_cipher):
    data_dir = tmp_path / "data"
    _store_token(data_dir, _info(scopes="https://evil.example/x"))
    with pytest.raises(AuthError, match="scope"):
        load_credentials(Settings(data_dir=data_dir))


def test_missing_cached_token_uri_rejected(tmp_path, identity_cipher):
    data_dir = tmp_path / "data"
    info = _info()
    del info["token_uri"]
    _store_token(data_dir, info)
    with pytest.raises(AuthError, match="token"):
        load_credentials(Settings(data_dir=data_dir))


def test_valid_load_no_refresh(tmp_path, identity_cipher):
    data_dir = tmp_path / "data"
    _store_token(data_dir, _info())
    with mock.patch.object(Credentials, "refresh") as refresh:
        creds = load_credentials(Settings(data_dir=data_dir))
    refresh.assert_not_called()
    assert set(creds.scopes) == set(SCOPES)


def test_refresh_does_not_launch_browser(tmp_path, identity_cipher):
    data_dir = tmp_path / "data"
    _store_token(data_dir, _info(expired=True))
    with mock.patch.object(Credentials, "refresh") as refresh:
        with mock.patch.object(auth, "Request") as request:
            with mock.patch.object(auth, "_store_credentials") as store:
                with mock.patch.object(
                    auth.InstalledAppFlow, "from_client_config"
                ) as fcc:
                    load_credentials(Settings(data_dir=data_dir))
    refresh.assert_called_once_with(request.return_value)
    store.assert_called_once()
    fcc.assert_not_called()


def test_missing_token_actionable(tmp_path):
    with pytest.raises(AuthError, match="authenticat"):
        load_credentials(Settings(data_dir=tmp_path / "data"))


def test_failed_refresh_sanitized(tmp_path, identity_cipher):
    data_dir = tmp_path / "data"
    _store_token(data_dir, _info(expired=True))
    with mock.patch.object(
        Credentials, "refresh", side_effect=Exception("secret /home/u/client.json boom")
    ):
        with pytest.raises(AuthError) as exc:
            load_credentials(Settings(data_dir=data_dir))
    message = str(exc.value)
    assert "secret" not in message
    assert "/home/u" not in message
    assert "client.json" not in message


def test_storage_roundtrip_fake_cipher(tmp_path, monkeypatch):
    key = 0x5A
    monkeypatch.setattr(auth, "_protect", lambda b: bytes(x ^ key for x in b))
    monkeypatch.setattr(auth, "_unprotect", lambda b: bytes(x ^ key for x in b))
    data_dir = tmp_path / "data"
    creds = _creds()
    auth._store_credentials(data_dir, creds)
    raw = (data_dir / "token.json").read_bytes()
    assert raw != creds.to_json().encode("utf-8")
    decrypted = auth._unprotect(raw)
    assert json.loads(decrypted.decode("utf-8"))["refresh_token"] == "refresh"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_posix_permissions(tmp_path, identity_cipher):
    data_dir = tmp_path / "data"
    auth._store_credentials(data_dir, _creds())
    assert (data_dir.stat().st_mode & 0o777) == 0o700
    assert (data_dir / "token.json").stat().st_mode & 0o777 == 0o600


def test_data_dir_inside_cwd_rejected():
    inside = Path.cwd() / "data"
    with pytest.raises(AuthError, match="outside the repository"):
        load_credentials(Settings(data_dir=inside))


def test_client_secrets_inside_cwd_rejected(tmp_path):
    inside = Path.cwd() / "client_secrets.json"
    with pytest.raises(AuthError, match="outside the repository"):
        authenticate(Settings(data_dir=tmp_path / "data"), inside)


def test_symlink_token_rejected(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    real = tmp_path / "real.json"
    real.write_bytes(b"{}")
    try:
        (data_dir / "token.json").symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    with pytest.raises(AuthError, match="symlink"):
        load_credentials(Settings(data_dir=data_dir))


def test_raw_oauth_exception_sanitized(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    secrets = tmp_path / "client_secrets.json"
    _write_secrets(secrets)
    secret = "client_secret=SUPERSECRET https://accounts.google.com/o/oauth2/auth"

    def boom(*args, **kwargs):
        raise RuntimeError(secret)

    with mock.patch.object(auth.InstalledAppFlow, "from_client_config", side_effect=boom):
        with pytest.raises(AuthError) as exc:
            authenticate(settings, secrets)
    message = str(exc.value)
    assert "SUPERSECRET" not in message
    assert "client_secret" not in message
    assert "accounts.google.com" not in message
    tb = "".join(
        traceback.format_exception(type(exc.value), exc.value, exc.value.__traceback__)
    )
    assert "SUPERSECRET" not in tb
    assert "accounts.google.com" not in tb


def test_source_root_rejected_when_cwd_elsewhere(tmp_path, monkeypatch):
    source_root = Path(auth.__file__).resolve().parents[1]
    if not (source_root / "pyproject.toml").exists() and not (source_root / ".git").exists():
        pytest.skip("source root has no project markers")
    monkeypatch.chdir(tmp_path)
    inside = source_root / "data"
    with pytest.raises(AuthError, match="outside the repository"):
        load_credentials(Settings(data_dir=inside))


def test_corrupted_token_json_rejected(tmp_path, identity_cipher):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "token.json").write_bytes(b"{not valid json")
    with pytest.raises(AuthError, match="corrupt"):
        load_credentials(Settings(data_dir=data_dir))


def test_from_authorized_user_info_error_sanitized(tmp_path, identity_cipher):
    data_dir = tmp_path / "data"
    _store_token(data_dir, _info())
    with mock.patch.object(
        Credentials,
        "from_authorized_user_info",
        side_effect=ValueError("secret /home/u/client.json boom"),
    ):
        with pytest.raises(AuthError) as exc:
            load_credentials(Settings(data_dir=data_dir))
    message = str(exc.value)
    assert "secret" not in message
    assert "/home/u" not in message
    assert "client.json" not in message


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI only")
def test_dpapi_roundtrip_real():
    plaintext = b'{"refresh_token":"synthetic","token_uri":"https://oauth2.googleapis.com/token"}'
    ciphertext = auth._dpapi_protect(plaintext)
    assert ciphertext != plaintext
    assert auth._dpapi_unprotect(ciphertext) == plaintext
