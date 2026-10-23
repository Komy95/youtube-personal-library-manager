"""Execution of reviewed migration plans against the YouTube account.

Writes are journaled in the ``migration_operations`` ledger before and after
each remote call so an interrupted run can be detected and refused instead of
blindly retried. A reserved ``__run__`` row claims the plan under
``BEGIN IMMEDIATE`` so that only a single executor can ever write.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError
from httplib2 import ServerNotFoundError

from youtube_manager.db import Database
from youtube_manager.planning import (
    Plan,
    _validate_operations,
    _verify_plan_hash,
    validate_plan,
)


class MigrationError(Exception):
    """Sanitized, user-safe migration failure."""

    def __init__(
        self,
        message: str = "",
        status: int | None = None,
        reason: str | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.reason = reason


class WriteUncertainError(Exception):
    """A write may or may not have reached YouTube; the outcome is unknown."""


_STARTED = "started"
_COMPLETED = "completed"
_FAILED = "failed"
_UNCERTAIN = "uncertain"

_RUN_OP = "__run__"

_SAFE_REASONS = frozenset(
    {
        "quotaExceeded",
        "dailyLimitExceeded",
        "insufficientPermissions",
        "forbidden",
        "playlistForbidden",
        "playlistNotFound",
        "videoNotFound",
        "playlistItemsNotAccessible",
        "playlistContainsMaximumNumberOfVideos",
        "playlistOperationUnsupported",
        "invalidResourceType",
        "videoIdRequired",
        "playlistIdRequired",
        "invalidPlaylistItemPosition",
        "rateLimitExceeded",
        "userRateLimitExceeded",
        "authError",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_reason(exc: HttpError) -> str | None:
    content = getattr(exc, "content", None)
    if isinstance(content, bytes):
        try:
            data = json.loads(content.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
    elif isinstance(content, str):
        try:
            data = json.loads(content)
        except ValueError:
            return None
    else:
        return None
    if not isinstance(data, dict):
        return None
    error = data.get("error")
    if not isinstance(error, dict):
        return None
    errors = error.get("errors")
    if not isinstance(errors, list):
        return None
    for err in errors:
        if not isinstance(err, dict):
            continue
        reason = err.get("reason")
        if isinstance(reason, str) and reason in _SAFE_REASONS:
            return reason
    return None


def _http_failure(exc: HttpError) -> MigrationError:
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None or status >= 500:
        raise WriteUncertainError("Remote service error.") from None
    reason = _extract_reason(exc)
    message = "Request rejected by YouTube."
    if status is not None:
        message += f" (HTTP {status})"
    if reason:
        message += f" ({reason})"
    return MigrationError(message, status=status, reason=reason)


class YouTubeWriter:
    """Executes exactly-once writes with no generic retry."""

    def __init__(self, service: Any):
        self.service = service

    def _execute(self, request: Any) -> Any:
        try:
            return request.execute(num_retries=0)
        except RefreshError:
            raise MigrationError("Authentication failed.") from None
        except HttpError as exc:
            raise _http_failure(exc) from None
        except (OSError, ServerNotFoundError):
            raise WriteUncertainError("Network error.") from None
        except Exception:
            raise WriteUncertainError("Unexpected write error.") from None

    def _execute_read(self, request: Any) -> Any:
        try:
            return request.execute()
        except RefreshError:
            raise MigrationError("Authentication failed.") from None
        except HttpError as exc:
            raise _http_failure(exc) from None
        except (OSError, ServerNotFoundError):
            raise WriteUncertainError("Network error.") from None
        except Exception:
            raise WriteUncertainError("Unexpected read error.") from None

    def create_playlist(self, title: str) -> dict:
        body = {
            "snippet": {"title": title},
            "status": {"privacyStatus": "private"},
        }
        request = self.service.playlists().insert(part="snippet,status", body=body)
        data = self._execute(request)
        pid = data.get("id") if isinstance(data, dict) else None
        if not pid:
            raise WriteUncertainError("Malformed response.")
        return {"playlist_id": pid}

    def add_video(self, playlist_id: str, video_id: str) -> dict:
        body = {
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        }
        request = self.service.playlistItems().insert(part="snippet", body=body)
        data = self._execute(request)
        item_id = data.get("id") if isinstance(data, dict) else None
        if not item_id:
            raise WriteUncertainError("Malformed response.")
        return {"item_id": item_id}

    def remove_item(self, item_id: str) -> dict:
        request = self.service.playlistItems().delete(id=item_id)
        self._execute(request)
        return {"deleted": True}

    def rename_playlist(self, playlist_id: str, title: str) -> dict:
        request = self.service.playlists().list(part="snippet", id=playlist_id)
        data = self._execute_read(request)
        items = (data or {}).get("items") or []
        if not items:
            raise MigrationError("Playlist not found.")
        item = items[0]
        if item.get("id") != playlist_id:
            raise MigrationError("Playlist lookup returned an unexpected id.")
        snippet = item.get("snippet") or {}
        new_snippet = {
            "title": title,
            "description": snippet.get("description", ""),
        }
        if snippet.get("defaultLanguage"):
            new_snippet["defaultLanguage"] = snippet["defaultLanguage"]
        request = self.service.playlists().update(
            part="snippet", body={"id": playlist_id, "snippet": new_snippet}
        )
        data = self._execute(request)
        updated_id = data.get("id") if isinstance(data, dict) else None
        if updated_id != playlist_id:
            raise WriteUncertainError("Malformed response.")
        return {"playlist_id": playlist_id, "title": title}


def _set_status(conn, plan_id: str, op_id: str, status: str, receipt: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO migration_operations "
        "(plan_id, operation_id, status, receipt_json, updated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(plan_id, operation_id) DO UPDATE SET "
        "status = excluded.status, receipt_json = excluded.receipt_json, "
        "updated_at = excluded.updated_at",
        (plan_id, op_id, status, json.dumps(receipt), _now()),
    )
    conn.commit()


def _begin_operation(conn, plan_id: str, op_id: str) -> bool:
    cur = conn.execute(
        "INSERT INTO migration_operations "
        "(plan_id, operation_id, status, receipt_json, updated_at) "
        "VALUES (?, ?, ?, 'null', ?) "
        "ON CONFLICT(plan_id, operation_id) DO NOTHING",
        (plan_id, op_id, _STARTED, _now()),
    )
    conn.commit()
    if cur.rowcount == 1:
        return True
    status = conn.execute(
        "SELECT status FROM migration_operations WHERE plan_id = ? AND operation_id = ?",
        (plan_id, op_id),
    ).fetchone()["status"]
    if status == _COMPLETED:
        return False
    raise MigrationError("Operation already in progress.")


def _mark_run(conn, plan_id: str, status: str) -> None:
    conn.execute(
        "UPDATE migration_operations SET status = ?, receipt_json = 'null', updated_at = ? "
        "WHERE plan_id = ? AND operation_id = ?",
        (status, _now(), plan_id, _RUN_OP),
    )
    conn.commit()


def _load_ledger(conn, plan_id: str) -> tuple[str | None, dict, dict]:
    run_status: str | None = None
    statuses: dict[str, str] = {}
    receipts: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT operation_id, status, receipt_json FROM migration_operations "
        "WHERE plan_id = ?",
        (plan_id,),
    ):
        if r["operation_id"] == _RUN_OP:
            run_status = r["status"]
            continue
        statuses[r["operation_id"]] = r["status"]
        receipts[r["operation_id"]] = json.loads(r["receipt_json"])
    return run_status, statuses, receipts


def _saved_plan_matches(db: Database, plan: Plan) -> bool:
    row = db._conn.execute(
        "SELECT data_json FROM migration_plans WHERE id = ?", (plan.id,)
    ).fetchone()
    if row is None:
        return False
    return json.loads(row["data_json"]) == json.loads(plan.model_dump_json())


def _all_completed(run_status: str | None, statuses: dict, op_ids: list[str]) -> bool:
    return (
        run_status == _COMPLETED
        and len(statuses) == len(op_ids)
        and all(statuses.get(i) == _COMPLETED for i in op_ids)
    )


def _perform(writer: Any, op: Any, playlist_id: str | None) -> dict:
    if op.kind == "create_playlist":
        receipt = writer.create_playlist(op.title)
        if not receipt.get("playlist_id"):
            raise WriteUncertainError("Malformed response.")
        return receipt
    if op.kind == "add_video":
        receipt = writer.add_video(playlist_id, op.video_id)
        if not receipt.get("item_id"):
            raise WriteUncertainError("Malformed response.")
        return receipt
    if op.kind == "remove_item":
        return writer.remove_item(op.item_id)
    if op.kind == "rename_playlist":
        receipt = writer.rename_playlist(playlist_id, op.title)
        if not receipt.get("playlist_id"):
            raise WriteUncertainError("Malformed response.")
        return receipt
    raise MigrationError("Unknown operation kind.")


_REFUSE_MESSAGE = (
    "Migration is partially applied or uncertain; inspect the "
    "remote account, re-sync, and create a new plan."
)


def execute_plan(
    db: Database,
    plan: Plan,
    writer: Any,
    approval: str,
    preflight: Callable[[], tuple[str, str]],
) -> dict:
    if plan.id != plan.hash or approval != plan.id:
        raise MigrationError("Approval does not match the plan.")

    if not _saved_plan_matches(db, plan):
        raise MigrationError("Plan is not saved or has been modified.")

    _verify_plan_hash(plan)
    _validate_operations(plan.operations)

    conn = db._conn
    op_ids = [op.id for op in plan.operations]

    conn.execute("BEGIN IMMEDIATE")
    try:
        run_status, statuses, receipts = _load_ledger(conn, plan.id)
        if _all_completed(run_status, statuses, op_ids):
            conn.commit()
            return {"plan_id": plan.id, "statuses": statuses, "receipts": receipts}
        if run_status is not None or statuses:
            raise MigrationError(_REFUSE_MESSAGE)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise

    validate_plan(db, plan)
    remote_account, remote_digest = preflight()
    if remote_account != plan.account_id or remote_digest != plan.inventory_digest:
        raise MigrationError("Remote state does not match the plan.")

    conn.execute("BEGIN IMMEDIATE")
    try:
        run_status, statuses, receipts = _load_ledger(conn, plan.id)
        if _all_completed(run_status, statuses, op_ids):
            conn.commit()
            return {"plan_id": plan.id, "statuses": statuses, "receipts": receipts}
        if run_status is not None or statuses:
            raise MigrationError(_REFUSE_MESSAGE)
        conn.execute(
            "INSERT INTO migration_operations "
            "(plan_id, operation_id, status, receipt_json, updated_at) "
            "VALUES (?, ?, ?, 'null', ?)",
            (plan.id, _RUN_OP, _STARTED, _now()),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise

    for op in plan.operations:
        run_status, statuses, receipts = _load_ledger(conn, plan.id)
        if statuses.get(op.id) == _COMPLETED:
            continue

        for dep in op.depends_on:
            if statuses.get(dep) != _COMPLETED:
                raise MigrationError("A dependency has not completed.")

        playlist_id = op.playlist_id
        if op.kind == "add_video" and op.playlist_ref is not None:
            ref_receipt = receipts.get(op.playlist_ref)
            if not ref_receipt or not ref_receipt.get("playlist_id"):
                raise MigrationError("Playlist reference is unresolved.")
            playlist_id = ref_receipt["playlist_id"]

        if not _begin_operation(conn, plan.id, op.id):
            continue

        try:
            receipt = _perform(writer, op, playlist_id)
        except WriteUncertainError:
            _set_status(conn, plan.id, op.id, _UNCERTAIN)
            _mark_run(conn, plan.id, _UNCERTAIN)
            raise
        except MigrationError as exc:
            failure_receipt: dict | None = None
            if exc.status is not None or exc.reason is not None:
                failure_receipt = {}
                if exc.status is not None:
                    failure_receipt["status"] = exc.status
                if exc.reason is not None:
                    failure_receipt["reason"] = exc.reason
            _set_status(conn, plan.id, op.id, _FAILED, failure_receipt)
            _mark_run(conn, plan.id, _FAILED)
            raise
        except Exception:
            _set_status(conn, plan.id, op.id, _UNCERTAIN)
            _mark_run(conn, plan.id, _UNCERTAIN)
            raise WriteUncertainError("Unexpected error during migration.") from None

        _set_status(conn, plan.id, op.id, _COMPLETED, receipt)
        receipts[op.id] = receipt

    _mark_run(conn, plan.id, _COMPLETED)
    _, statuses, receipts = _load_ledger(conn, plan.id)
    return {"plan_id": plan.id, "statuses": statuses, "receipts": receipts}
