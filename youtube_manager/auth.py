"""Google desktop OAuth flow and token management.

Importing this module performs no I/O and launches no browser.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from youtube_manager.config import Settings

SCOPES = ("https://www.googleapis.com/auth/youtube.readonly",)

_AUTH_URIS = frozenset(
    {
        "https://accounts.google.com/o/oauth2/auth",
        "https://accounts.google.com/o/oauth2/v2/auth",
    }
)
_TOKEN_URI = "https://oauth2.googleapis.com/token"
_INSTALLED = "installed"
_SOURCE_ROOT = Path(__file__).resolve().parents[1]


class AuthError(Exception):
    """Raised for authentication failures. Messages are always sanitized."""


def _outside_cwd(path: Path, what: str) -> Path:
    resolved = path.resolve()
    cwd = Path.cwd().resolve()
    if resolved == cwd or resolved.is_relative_to(cwd):
        raise AuthError(
            f"The {what} resolves inside the repository. Move it outside the "
            f"repository directory."
        )
    if (_SOURCE_ROOT / "pyproject.toml").exists() or (_SOURCE_ROOT / ".git").exists():
        if resolved == _SOURCE_ROOT or resolved.is_relative_to(_SOURCE_ROOT):
            raise AuthError(
                f"The {what} resolves inside the repository. Move it outside the "
                f"repository directory."
            )
    return resolved


def _validate_client_config(config: Any) -> dict:
    if not isinstance(config, dict):
        raise AuthError("Client secrets are invalid.")
    if "web" in config:
        raise AuthError("Client secrets must be an 'installed' app, not a 'web' app.")
    if _INSTALLED not in config:
        raise AuthError("Client secrets must contain an 'installed' app.")
    installed = config[_INSTALLED]
    if not isinstance(installed, dict):
        raise AuthError("Invalid 'installed' client configuration.")
    if installed.get("auth_uri") not in _AUTH_URIS:
        raise AuthError("Client secrets specify an invalid authorization endpoint.")
    if installed.get("token_uri") != _TOKEN_URI:
        raise AuthError("Client secrets specify an invalid token endpoint.")
    return config


def _load_client_config(path: Path) -> dict:
    _outside_cwd(path, "client secrets file")
    if path.is_symlink():
        raise AuthError("The client secrets file must not be a symlink.")
    with path.open("r", encoding="utf-8") as fh:
        return _validate_client_config(json.load(fh))


def _validate_scopes(creds: Credentials) -> None:
    expected = set(SCOPES)
    if set(creds.scopes or ()) != expected:
        raise AuthError("Credentials contain unexpected scopes.")
    granted = getattr(creds, "granted_scopes", None)
    if granted is not None and set(granted) != expected:
        raise AuthError("Credentials contain unexpected granted scopes.")


def _validate_token_uri(creds: Credentials) -> None:
    token_uri = getattr(creds, "token_uri", None)
    if token_uri is not None and token_uri != _TOKEN_URI:
        raise AuthError("Credentials contain an invalid token endpoint.")


def _validate_cached_info(info: Any) -> None:
    if not isinstance(info, dict):
        raise AuthError("Stored credentials are corrupt.")
    if "scopes" in info:
        scopes = info["scopes"]
        if not isinstance(scopes, (list, tuple, set)) or not all(
            isinstance(s, str) for s in scopes
        ):
            raise AuthError("Stored credentials contain invalid scopes.")
        granted = set(scopes)
    elif info.get("scope"):
        granted = set(str(info["scope"]).split())
    else:
        raise AuthError("Stored credentials are missing scopes.")
    if granted != set(SCOPES):
        raise AuthError("Stored credentials contain unexpected scopes.")
    token_uri = info.get("token_uri")
    if token_uri != _TOKEN_URI:
        raise AuthError("Stored credentials contain an invalid token endpoint.")
    granted_scopes = info.get("granted_scopes")
    if granted_scopes is not None:
        if not isinstance(granted_scopes, (list, tuple, set)) or not all(
            isinstance(s, str) for s in granted_scopes
        ):
            raise AuthError("Stored credentials contain invalid granted scopes.")
        if set(granted_scopes) != set(SCOPES):
            raise AuthError("Stored credentials contain unexpected granted scopes.")


if sys.platform == "win32":
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    _crypt32 = ctypes.WinDLL("crypt32")
    _kernel32 = ctypes.WinDLL("kernel32")

    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    def _bytes_from_blob(blob: _DATA_BLOB) -> bytes:
        try:
            return ctypes.string_at(blob.pbData, blob.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))

    def _dpapi_protect(data: bytes) -> bytes:
        buf = ctypes.create_string_buffer(data)
        in_blob = _DATA_BLOB()
        in_blob.cbData = len(data)
        in_blob.pbData = ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))
        out_blob = _DATA_BLOB()
        ok = _crypt32.CryptProtectData(
            ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
        )
        if not ok:
            raise AuthError("Unable to secure credentials on this device.")
        return _bytes_from_blob(out_blob)

    def _dpapi_unprotect(data: bytes) -> bytes:
        buf = ctypes.create_string_buffer(data)
        in_blob = _DATA_BLOB()
        in_blob.cbData = len(data)
        in_blob.pbData = ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))
        out_blob = _DATA_BLOB()
        ok = _crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
        )
        if not ok:
            raise AuthError("Unable to decrypt stored credentials.")
        return _bytes_from_blob(out_blob)


def _protect(data: bytes) -> bytes:
    if sys.platform == "win32":
        return _dpapi_protect(data)
    return data


def _unprotect(data: bytes) -> bytes:
    if sys.platform == "win32":
        return _dpapi_unprotect(data)
    return data


def _resolved_data_dir(settings: Settings) -> Path:
    data_dir = settings.data_dir
    _outside_cwd(data_dir, "data directory")
    if data_dir.is_symlink():
        raise AuthError("The data directory must not be a symlink.")
    return data_dir.resolve()


def _secure_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".token", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            if sys.platform != "win32":
                os.fchmod(fh.fileno(), 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _store_credentials(data_dir: Path, creds: Credentials) -> None:
    token_path = data_dir / "token.json"
    if token_path.is_symlink():
        raise AuthError("The token file must not be a symlink.")
    payload = creds.to_json().encode("utf-8")
    _secure_write(token_path, _protect(payload))


def authenticate(settings: Settings, client_secrets: Path) -> Credentials:
    data_dir = _resolved_data_dir(settings)
    config = _load_client_config(client_secrets)
    try:
        flow = InstalledAppFlow.from_client_config(
            config, scopes=SCOPES, autogenerate_code_verifier=True
        )
        creds = flow.run_local_server(
            host="127.0.0.1",
            port=0,
            open_browser=True,
            timeout_seconds=180,
            authorization_prompt_message="",
            prompt="consent",
            access_type="offline",
        )
    except AuthError:
        raise
    except Exception:
        raise AuthError(
            "Authorization failed. Check your client secrets file and try again."
        ) from None
    if not getattr(creds, "refresh_token", None):
        raise AuthError("Authorization did not return a refresh token.")
    _validate_scopes(creds)
    _validate_token_uri(creds)
    _store_credentials(data_dir, creds)
    return creds


def load_credentials(settings: Settings) -> Credentials:
    data_dir = _resolved_data_dir(settings)
    token_path = data_dir / "token.json"
    if not token_path.exists():
        raise AuthError("No stored credentials found. Run authentication first.")
    if token_path.is_symlink():
        raise AuthError("The token file must not be a symlink.")
    try:
        info = json.loads(_unprotect(token_path.read_bytes()).decode("utf-8"))
    except AuthError:
        raise
    except (ValueError, UnicodeDecodeError, OSError):
        raise AuthError("Stored credentials are corrupt. Re-authenticate.") from None
    _validate_cached_info(info)
    try:
        creds = Credentials.from_authorized_user_info(info, scopes=SCOPES)
    except Exception:
        raise AuthError("Stored credentials are corrupt. Re-authenticate.") from None
    if not creds.valid:
        try:
            creds.refresh(Request())
        except Exception:
            raise AuthError("Unable to refresh credentials. Re-authenticate.") from None
        _validate_scopes(creds)
        _validate_token_uri(creds)
        _store_credentials(data_dir, creds)
    else:
        _validate_scopes(creds)
        _validate_token_uri(creds)
    return creds
