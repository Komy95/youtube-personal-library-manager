"""Write-scope Google OAuth flow and token management.

This module grants the *write* scope (``youtube.force-ssl``) for the
reviewable-reorganization phase. Every entrypoint requires an explicit,
previously-saved :class:`~youtube_manager.planning.Plan` whose ``id`` and
``hash`` both equal the caller-supplied ``approved_plan`` digest, and which
still validates against the current local inventory. No browser is opened and
no token is read until that plan check succeeds.

Importing this module performs no I/O and launches no browser.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from youtube_manager.auth import (
    AuthError,
    _load_client_config,
    _protect,
    _resolved_data_dir,
    _secure_write,
    _unprotect,
)
from youtube_manager.config import Settings
from youtube_manager.db import Database
from youtube_manager.planning import load_plan, validate_plan

WRITE_SCOPES = ("https://www.googleapis.com/auth/youtube.force-ssl",)

_TOKEN_URI = "https://oauth2.googleapis.com/token"
_WRITE_TOKEN_FILENAME = "write_token.json"


def _validate_approved_plan(settings: Settings, approved_plan: str) -> None:
    if not isinstance(approved_plan, str) or len(approved_plan) != 64:
        raise AuthError("approved_plan must be a 64-character hexadecimal digest.")
    try:
        int(approved_plan, 16)
    except ValueError:
        raise AuthError("approved_plan must be a 64-character hexadecimal digest.")

    db = Database(settings.db_path)
    try:
        try:
            plan = load_plan(db, approved_plan)
        except KeyError:
            raise AuthError(
                "No approved plan found with that id. Run planning first."
            ) from None
        except ValueError:
            raise AuthError("The stored plan is corrupt.") from None
        if plan.id != approved_plan or plan.hash != approved_plan:
            raise AuthError("The plan does not match the approved plan id.")
        try:
            validate_plan(db, plan)
        except ValueError:
            raise AuthError(
                "The approved plan no longer matches the current inventory. "
                "Rebuild it."
            ) from None
    finally:
        db.close()


def _validate_write_scopes(creds: Credentials) -> None:
    expected = set(WRITE_SCOPES)
    if set(creds.scopes or ()) != expected:
        raise AuthError("Credentials contain unexpected scopes.")
    granted = getattr(creds, "granted_scopes", None)
    if granted is not None and set(granted) != expected:
        raise AuthError("Credentials contain unexpected granted scopes.")


def _validate_write_token_uri(creds: Credentials) -> None:
    token_uri = getattr(creds, "token_uri", None)
    if token_uri is not None and token_uri != _TOKEN_URI:
        raise AuthError("Credentials contain an invalid token endpoint.")


def _validate_write_cached_info(info: Any) -> None:
    if not isinstance(info, dict):
        raise AuthError("Stored credentials are corrupt.")

    expected = set(WRITE_SCOPES)

    scopes_value = info.get("scopes", None)
    if scopes_value is not None:
        if not isinstance(scopes_value, (list, tuple, set)) or not all(
            isinstance(s, str) for s in scopes_value
        ):
            raise AuthError("Stored credentials contain invalid scopes.")
        scopes_set = set(scopes_value)
    else:
        scopes_set = None

    scope_value = info.get("scope", None)
    if scope_value is not None:
        if not isinstance(scope_value, str):
            raise AuthError("Stored credentials contain invalid scope.")
        scope_set = set(scope_value.split())
    else:
        scope_set = None

    if scopes_set is None and scope_set is None:
        raise AuthError("Stored credentials are missing scopes.")
    if scopes_set is not None and scope_set is not None and scopes_set != scope_set:
        raise AuthError("Stored credentials contain conflicting scopes.")
    granted = scopes_set if scopes_set is not None else scope_set
    if granted != expected:
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
        if set(granted_scopes) != set(WRITE_SCOPES):
            raise AuthError("Stored credentials contain unexpected granted scopes.")


def _store_write_credentials(data_dir: Path, creds: Credentials) -> None:
    token_path = data_dir / _WRITE_TOKEN_FILENAME
    if token_path.is_symlink():
        raise AuthError("The write token file must not be a symlink.")
    payload = creds.to_json().encode("utf-8")
    _secure_write(token_path, _protect(payload))


def authenticate_write(
    settings: Settings, client_secrets: Path, approved_plan: str
) -> Credentials:
    _validate_approved_plan(settings, approved_plan)
    data_dir = _resolved_data_dir(settings)
    config = _load_client_config(client_secrets)
    try:
        flow = InstalledAppFlow.from_client_config(
            config, scopes=WRITE_SCOPES, autogenerate_code_verifier=True
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
    _validate_write_scopes(creds)
    _validate_write_token_uri(creds)
    _store_write_credentials(data_dir, creds)
    return creds


def load_write_credentials(settings: Settings, approved_plan: str) -> Credentials:
    _validate_approved_plan(settings, approved_plan)
    data_dir = _resolved_data_dir(settings)
    token_path = data_dir / _WRITE_TOKEN_FILENAME
    if not token_path.exists():
        raise AuthError(
            "No stored write credentials found. Run authentication first."
        )
    if token_path.is_symlink():
        raise AuthError("The write token file must not be a symlink.")
    try:
        info = json.loads(_unprotect(token_path.read_bytes()).decode("utf-8"))
    except AuthError:
        raise
    except (ValueError, UnicodeDecodeError, OSError):
        raise AuthError("Stored credentials are corrupt. Re-authenticate.") from None
    _validate_write_cached_info(info)
    try:
        creds = Credentials.from_authorized_user_info(info, scopes=WRITE_SCOPES)
    except Exception:
        raise AuthError("Stored credentials are corrupt. Re-authenticate.") from None
    if not creds.valid:
        try:
            creds.refresh(Request())
        except Exception:
            raise AuthError(
                "Unable to refresh credentials. Re-authenticate."
            ) from None
        _validate_write_scopes(creds)
        _validate_write_token_uri(creds)
        _store_write_credentials(data_dir, creds)
    else:
        _validate_write_scopes(creds)
        _validate_write_token_uri(creds)
    return creds
