"""Tests for plan execution and the YouTube write layer."""

from __future__ import annotations

import json

import pytest
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from youtube_manager.db import Database
from youtube_manager.migration import (
    MigrationError,
    WriteUncertainError,
    YouTubeWriter,
    _extract_reason,
    execute_plan,
)
from youtube_manager.models import Playlist, PlaylistItem, Snapshot, Video
from youtube_manager.planning import (
    Operation,
    Plan,
    _compute_plan_hash,
    inventory_digest,
    save_plan,
)


class _Resp:
    def __init__(self, status):
        self.status = status
        self.reason = "test-reason"


class FakeRequest:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.execute_kwargs = None

    def execute(self, **kwargs):
        self.execute_kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.result


class FakeService:
    def __init__(self):
        self.insert_calls = []
        self.update_calls = []
        self.list_calls = []
        self.item_insert_calls = []
        self.delete_calls = []
        self.list_result = None
        self.update_result = None
        self.delete_result = None
        self.insert_error = None
        self.list_error = None
        self._insert_results = []

    def playlists(self):
        return _FakePlaylists(self)

    def playlistItems(self):
        return _FakePlaylistItems(self)


class _FakePlaylists:
    def __init__(self, service):
        self.service = service

    def insert(self, part, body):
        result = self.service._insert_results.pop(0) if self.service._insert_results else {"id": "PL_NEW"}
        req = FakeRequest(result, error=self.service.insert_error)
        self.service.insert_calls.append((part, body, req))
        return req

    def update(self, part, body):
        req = FakeRequest(self.service.update_result)
        self.service.update_calls.append((part, body, req))
        return req

    def list(self, part, id):
        req = FakeRequest(self.service.list_result, error=self.service.list_error)
        self.service.list_calls.append((part, id))
        return req


class _FakePlaylistItems:
    def __init__(self, service):
        self.service = service

    def insert(self, part, body):
        req = FakeRequest({"id": "ITEM_NEW"})
        self.service.item_insert_calls.append((part, body, req))
        return req

    def delete(self, id):
        req = FakeRequest(self.service.delete_result)
        self.service.delete_calls.append(id)
        return req


class FakeWriter:
    def __init__(self):
        self.calls = []
        self.fail_on = {}
        self.uncertain_on = {}

    def create_playlist(self, title):
        self.calls.append(("create_playlist", title))
        self._maybe_raise("create_playlist")
        return {"playlist_id": "PL_NEW"}

    def add_video(self, playlist_id, video_id):
        self.calls.append(("add_video", playlist_id, video_id))
        self._maybe_raise("add_video")
        return {"item_id": "ITEM_NEW"}

    def remove_item(self, item_id):
        self.calls.append(("remove_item", item_id))
        self._maybe_raise("remove_item")
        return {"deleted": True}

    def rename_playlist(self, playlist_id, title):
        self.calls.append(("rename_playlist", playlist_id, title))
        self._maybe_raise("rename_playlist")
        return {"playlist_id": playlist_id, "title": title}

    def _maybe_raise(self, kind):
        if kind in self.uncertain_on:
            raise WriteUncertainError("boom")
        if kind in self.fail_on:
            raise MigrationError("rejected")


class ReentrantWriter:
    def __init__(self):
        self.calls = []
        self.on_first = None
        self.fired = False

    def create_playlist(self, title):
        self.calls.append(("create_playlist", title))
        if not self.fired:
            self.fired = True
            if self.on_first is not None:
                self.on_first()
        return {"playlist_id": "PL_NEW"}

    def add_video(self, playlist_id, video_id):
        self.calls.append(("add_video", playlist_id, video_id))
        return {"item_id": "ITEM_NEW"}

    def remove_item(self, item_id):
        self.calls.append(("remove_item", item_id))
        return {"deleted": True}

    def rename_playlist(self, playlist_id, title):
        self.calls.append(("rename_playlist", playlist_id, title))
        return {"playlist_id": playlist_id, "title": title}


def make_snapshot():
    return Snapshot(
        account_id="acct1",
        playlists=[Playlist(id="PL1", title="My List", item_count=2)],
        videos=[Video(id="v1", title="V1"), Video(id="v2", title="V2")],
        items=[
            PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0),
            PlaylistItem(id="i2", playlist_id="PL1", video_id="v2", position=1),
        ],
    )


def seeded_db(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    db.apply_snapshot(make_snapshot())
    return db


def default_ops():
    return [
        Operation(id="create_playlist_1", kind="create_playlist", title="Target"),
        Operation(
            id="add_video_1_1",
            kind="add_video",
            playlist_ref="create_playlist_1",
            video_id="v1",
            depends_on=["create_playlist_1"],
        ),
    ]


def make_plan(db, operations, description=""):
    digest = inventory_digest(db)
    h = _compute_plan_hash("acct1", digest, operations, description)
    return Plan(
        id=h,
        hash=h,
        account_id="acct1",
        inventory_digest=digest,
        operations=operations,
        created_at="2026-01-01T00:00:00+00:00",
        description=description,
    )


def save_and(db, operations):
    plan = make_plan(db, operations)
    save_plan(db, plan)
    return plan


# -- YouTubeWriter -----------------------------------------------------------


def test_create_playlist_private_and_no_retry():
    svc = FakeService()
    writer = YouTubeWriter(svc)
    assert writer.create_playlist("Target") == {"playlist_id": "PL_NEW"}
    part, body, req = svc.insert_calls[0]
    assert part == "snippet,status"
    assert body["snippet"] == {"title": "Target"}
    assert body["status"] == {"privacyStatus": "private"}
    assert req.execute_kwargs == {"num_retries": 0}


def test_add_video_body_and_no_retry():
    svc = FakeService()
    writer = YouTubeWriter(svc)
    assert writer.add_video("PL1", "v1") == {"item_id": "ITEM_NEW"}
    _, body, req = svc.item_insert_calls[0]
    assert body["snippet"]["playlistId"] == "PL1"
    assert body["snippet"]["resourceId"] == {"kind": "youtube#video", "videoId": "v1"}
    assert req.execute_kwargs == {"num_retries": 0}


def test_remove_item_no_retry():
    svc = FakeService()
    writer = YouTubeWriter(svc)
    assert writer.remove_item("i1") == {"deleted": True}
    assert svc.delete_calls == ["i1"]


def test_rename_retains_description_and_default_language():
    svc = FakeService()
    svc.list_result = {
        "items": [
            {
                "id": "PL1",
                "snippet": {
                    "title": "Old",
                    "description": "keep me",
                    "defaultLanguage": "en",
                },
            }
        ]
    }
    svc.update_result = {"id": "PL1"}
    writer = YouTubeWriter(svc)
    assert writer.rename_playlist("PL1", "New") == {
        "playlist_id": "PL1",
        "title": "New",
    }
    _, body, _ = svc.update_calls[0]
    assert body["snippet"]["title"] == "New"
    assert body["snippet"]["description"] == "keep me"
    assert body["snippet"]["defaultLanguage"] == "en"


def test_definite_4xx_is_sanitized():
    svc = FakeService()
    svc.insert_error = HttpError(_Resp(400), b"raw secret body")
    writer = YouTubeWriter(svc)
    with pytest.raises(MigrationError) as ei:
        writer.create_playlist("T")
    assert "secret" not in str(ei.value)


def test_5xx_is_uncertain():
    svc = FakeService()
    svc.insert_error = HttpError(_Resp(500), b"boom")
    writer = YouTubeWriter(svc)
    with pytest.raises(WriteUncertainError):
        writer.create_playlist("T")


def test_transport_error_is_uncertain():
    svc = FakeService()
    svc.insert_error = OSError("network down")
    writer = YouTubeWriter(svc)
    with pytest.raises(WriteUncertainError):
        writer.create_playlist("T")


def test_malformed_success_is_uncertain():
    svc = FakeService()
    svc._insert_results = [{}]
    writer = YouTubeWriter(svc)
    with pytest.raises(WriteUncertainError):
        writer.create_playlist("T")


# -- execute_plan ------------------------------------------------------------


def test_wrong_approval_no_calls(tmp_path):
    db = seeded_db(tmp_path)
    try:
        plan = save_and(db, default_ops())
        writer = FakeWriter()
        with pytest.raises(MigrationError):
            execute_plan(db, plan, writer, "wrong", lambda: ("acct1", plan.inventory_digest))
        assert writer.calls == []
        assert db._conn.execute("SELECT * FROM migration_operations").fetchall() == []
    finally:
        db.close()


def test_unsaved_plan_no_calls(tmp_path):
    db = seeded_db(tmp_path)
    try:
        plan = make_plan(db, default_ops())
        writer = FakeWriter()
        with pytest.raises(MigrationError):
            execute_plan(db, plan, writer, plan.id, lambda: ("acct1", plan.inventory_digest))
        assert writer.calls == []
    finally:
        db.close()


def test_tampered_plan_no_calls(tmp_path):
    db = seeded_db(tmp_path)
    try:
        plan = save_and(db, default_ops())
        tampered = plan.model_copy(update={"operations": default_ops() + [Operation(id="x", kind="create_playlist", title="X")]})
        writer = FakeWriter()
        with pytest.raises(MigrationError):
            execute_plan(db, tampered, writer, plan.id, lambda: ("acct1", plan.inventory_digest))
        assert writer.calls == []
    finally:
        db.close()


def test_stale_preflight_no_writes_or_started(tmp_path):
    db = seeded_db(tmp_path)
    try:
        plan = save_and(db, default_ops())
        writer = FakeWriter()
        with pytest.raises(MigrationError):
            execute_plan(db, plan, writer, plan.id, lambda: ("acct1", "stale-digest"))
        assert writer.calls == []
        assert db._conn.execute("SELECT * FROM migration_operations").fetchall() == []
    finally:
        db.close()


def test_wrong_account_preflight_no_writes(tmp_path):
    db = seeded_db(tmp_path)
    try:
        plan = save_and(db, default_ops())
        writer = FakeWriter()
        with pytest.raises(MigrationError):
            execute_plan(db, plan, writer, plan.id, lambda: ("other-acct", plan.inventory_digest))
        assert writer.calls == []
    finally:
        db.close()


def test_write_order_and_reference_resolution(tmp_path):
    db = seeded_db(tmp_path)
    try:
        ops = default_ops() + [
            Operation(
                id="remove_item_1",
                kind="remove_item",
                item_id="i1",
                depends_on=["add_video_1_1"],
            )
        ]
        plan = save_and(db, ops)
        writer = FakeWriter()
        result = execute_plan(db, plan, writer, plan.id, lambda: ("acct1", plan.inventory_digest))
        assert [c[0] for c in writer.calls] == ["create_playlist", "add_video", "remove_item"]
        assert writer.calls[1] == ("add_video", "PL_NEW", "v1")
        assert result["statuses"]["remove_item_1"] == "completed"
        assert result["receipts"]["add_video_1_1"] == {"item_id": "ITEM_NEW"}
    finally:
        db.close()


def test_fully_completed_rerun_is_noop(tmp_path):
    db = seeded_db(tmp_path)
    try:
        plan = save_and(db, default_ops())
        writer = FakeWriter()
        execute_plan(db, plan, writer, plan.id, lambda: ("acct1", plan.inventory_digest))
        assert len(writer.calls) == 2
        writer2 = FakeWriter()
        result = execute_plan(db, plan, writer2, plan.id, lambda: ("acct1", plan.inventory_digest))
        assert writer2.calls == []
        assert set(result["statuses"].values()) == {"completed"}
    finally:
        db.close()


def test_failure_prevents_removals(tmp_path):
    db = seeded_db(tmp_path)
    try:
        ops = default_ops() + [
            Operation(id="remove_item_1", kind="remove_item", item_id="i1", depends_on=["add_video_1_1"])
        ]
        plan = save_and(db, ops)
        writer = FakeWriter()
        writer.fail_on = {"add_video"}
        with pytest.raises(MigrationError):
            execute_plan(db, plan, writer, plan.id, lambda: ("acct1", plan.inventory_digest))
        assert [c[0] for c in writer.calls] == ["create_playlist", "add_video"]
        statuses, _ = _ledger(db, plan.id)
        assert statuses["add_video_1_1"] == "failed"
        assert "remove_item_1" not in statuses
    finally:
        db.close()


def test_interrupted_started_refuses(tmp_path):
    db = seeded_db(tmp_path)
    try:
        plan = save_and(db, default_ops())
        db._conn.execute(
            "INSERT INTO migration_operations (plan_id, operation_id, status, receipt_json, updated_at) "
            "VALUES (?, '__run__', 'started', 'null', 't')",
            (plan.id,),
        )
        db._conn.execute(
            "INSERT INTO migration_operations (plan_id, operation_id, status, receipt_json, updated_at) "
            "VALUES (?, ?, 'started', 'null', 't')",
            (plan.id, "create_playlist_1"),
        )
        db._conn.commit()
        writer = FakeWriter()
        with pytest.raises(MigrationError):
            execute_plan(db, plan, writer, plan.id, lambda: ("acct1", plan.inventory_digest))
        assert writer.calls == []
    finally:
        db.close()


def test_uncertain_journaled_and_replay_refused(tmp_path):
    db = seeded_db(tmp_path)
    try:
        plan = save_and(db, default_ops())
        writer = FakeWriter()
        writer.uncertain_on = {"add_video"}
        with pytest.raises(WriteUncertainError):
            execute_plan(db, plan, writer, plan.id, lambda: ("acct1", plan.inventory_digest))
        statuses, _ = _ledger(db, plan.id)
        assert statuses["add_video_1_1"] == "uncertain"

        writer2 = FakeWriter()
        with pytest.raises(MigrationError):
            execute_plan(db, plan, writer2, plan.id, lambda: ("acct1", plan.inventory_digest))
        assert writer2.calls == []
    finally:
        db.close()


def test_rename_read_4xx_is_sanitized():
    svc = FakeService()
    svc.list_error = HttpError(_Resp(400), b"raw secret body")
    writer = YouTubeWriter(svc)
    with pytest.raises(MigrationError) as ei:
        writer.rename_playlist("PL1", "New")
    assert "secret" not in str(ei.value)


def test_rename_requires_matching_returned_id():
    svc = FakeService()
    svc.list_result = {"items": [{"id": "OTHER", "snippet": {"title": "Old"}}]}
    svc.update_result = {"id": "OTHER"}
    writer = YouTubeWriter(svc)
    with pytest.raises(MigrationError):
        writer.rename_playlist("PL1", "New")


def test_concurrent_executors_single_writer(tmp_path):
    path = tmp_path / "inventory.sqlite3"
    db1 = Database(path)
    db2 = Database(path)
    try:
        db1.apply_snapshot(make_snapshot())
        plan = save_and(db1, default_ops())

        writer1 = ReentrantWriter()
        writer2 = FakeWriter()
        captured = []

        def reenter():
            try:
                execute_plan(
                    db2, plan, writer2, plan.id,
                    lambda: ("acct1", plan.inventory_digest),
                )
            except MigrationError as exc:
                captured.append(str(exc))

        writer1.on_first = reenter
        result = execute_plan(
            db1, plan, writer1, plan.id,
            lambda: ("acct1", plan.inventory_digest),
        )

        assert captured
        assert writer2.calls == []
        assert len(writer1.calls) == 2
        statuses, receipts = _ledger(db1, plan.id)
        assert statuses["create_playlist_1"] == "completed"
        assert statuses["add_video_1_1"] == "completed"
        assert len([k for k in statuses if k.startswith("create_playlist")]) == 1
    finally:
        db1.close()
        db2.close()


def test_reserved_claim_blocks_empty_ops(tmp_path):
    db = seeded_db(tmp_path)
    try:
        plan = save_and(db, default_ops())
        db._conn.execute(
            "INSERT INTO migration_operations (plan_id, operation_id, status, receipt_json, updated_at) "
            "VALUES (?, '__run__', 'started', 'null', 't')",
            (plan.id,),
        )
        db._conn.commit()
        writer = FakeWriter()
        with pytest.raises(MigrationError):
            execute_plan(db, plan, writer, plan.id, lambda: ("acct1", plan.inventory_digest))
        assert writer.calls == []
    finally:
        db.close()


def test_completed_replay_skips_preflight_after_sync(tmp_path):
    db = seeded_db(tmp_path)
    try:
        plan = save_and(db, default_ops())
        writer = FakeWriter()
        execute_plan(db, plan, writer, plan.id, lambda: ("acct1", plan.inventory_digest))
        assert len(writer.calls) == 2

        db.apply_snapshot(
            Snapshot(
                account_id="acct1",
                playlists=[Playlist(id="PL1", title="My List", item_count=3)],
                videos=[
                    Video(id="v1", title="V1"),
                    Video(id="v2", title="V2"),
                    Video(id="v3", title="V3"),
                ],
                items=[
                    PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0),
                    PlaylistItem(id="i2", playlist_id="PL1", video_id="v2", position=1),
                    PlaylistItem(id="i3", playlist_id="PL1", video_id="v3", position=2),
                ],
            )
        )

        def boom():
            raise AssertionError("preflight must not run on completed replay")

        writer2 = FakeWriter()
        result = execute_plan(db, plan, writer2, plan.id, boom)
        assert writer2.calls == []
        assert set(result["statuses"].values()) == {"completed"}
    finally:
        db.close()


def _ledger(db, plan_id):
    statuses, receipts = {}, {}
    for r in db._conn.execute(
        "SELECT operation_id, status, receipt_json FROM migration_operations WHERE plan_id = ?",
        (plan_id,),
    ):
        statuses[r["operation_id"]] = r["status"]
        receipts[r["operation_id"]] = json.loads(r["receipt_json"])
    return statuses, receipts


# -- existing target execution ----------------------------------------------


def make_existing_snapshot():
    return Snapshot(
        account_id="acct1",
        playlists=[
            Playlist(id="PL1", title="My List", channel_id="acct1", item_count=2),
        ],
        videos=[Video(id="v1", title="V1"), Video(id="v2", title="V2")],
        items=[
            PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0),
            PlaylistItem(id="i2", playlist_id="PL1", video_id="v2", position=1),
        ],
    )


def seeded_existing_db(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    db.apply_snapshot(make_existing_snapshot())
    return db


def test_execute_add_video_direct_playlist_id(tmp_path):
    db = seeded_existing_db(tmp_path)
    try:
        ops = [
            Operation(
                id="add_video_1",
                kind="add_video",
                playlist_id="PL1",
                video_id="v2",
            )
        ]
        plan = save_and(db, ops)
        writer = FakeWriter()
        result = execute_plan(
            db, plan, writer, plan.id, lambda: ("acct1", plan.inventory_digest)
        )
        assert writer.calls == [("add_video", "PL1", "v2")]
        assert result["receipts"]["add_video_1"] == {"item_id": "ITEM_NEW"}
    finally:
        db.close()


# -- sanitized error details ------------------------------------------------


def test_4xx_recognized_reason_exposed():
    svc = FakeService()
    body = json.dumps(
        {"error": {"errors": [{"reason": "quotaExceeded", "message": "internal secret"}]}}
    ).encode()
    svc.insert_error = HttpError(_Resp(403), body)
    writer = YouTubeWriter(svc)
    with pytest.raises(MigrationError) as ei:
        writer.create_playlist("T")
    assert ei.value.status == 403
    assert ei.value.reason == "quotaExceeded"
    assert "Request rejected by YouTube" in str(ei.value)
    assert "quotaExceeded" in str(ei.value)
    assert "secret" not in str(ei.value)


def test_4xx_unknown_reason_omitted():
    svc = FakeService()
    body = json.dumps(
        {"error": {"errors": [{"reason": "someUnknownReason", "message": "internal secret"}]}}
    ).encode()
    svc.insert_error = HttpError(_Resp(400), body)
    writer = YouTubeWriter(svc)
    with pytest.raises(MigrationError) as ei:
        writer.create_playlist("T")
    assert ei.value.status == 400
    assert ei.value.reason is None
    assert "someUnknownReason" not in str(ei.value)
    assert "secret" not in str(ei.value)


def test_raw_body_never_exposed():
    svc = FakeService()
    body = json.dumps(
        {"error": {"errors": [{"reason": "forbidden", "message": "TOP-SECRET-PAYLOAD"}]}}
    ).encode()
    svc.insert_error = HttpError(_Resp(403), body)
    writer = YouTubeWriter(svc)
    with pytest.raises(MigrationError) as ei:
        writer.create_playlist("T")
    assert "TOP-SECRET-PAYLOAD" not in str(ei.value)
    assert ei.value.reason == "forbidden"


def test_5xx_recognized_reason_still_uncertain():
    svc = FakeService()
    body = json.dumps(
        {"error": {"errors": [{"reason": "rateLimitExceeded", "message": "x"}]}}
    ).encode()
    svc.insert_error = HttpError(_Resp(503), body)
    writer = YouTubeWriter(svc)
    with pytest.raises(WriteUncertainError):
        writer.create_playlist("T")


def test_failed_4xx_reason_journaled(tmp_path):
    db = seeded_db(tmp_path)
    try:
        ops = [Operation(id="create_playlist_1", kind="create_playlist", title="Target")]
        plan = save_and(db, ops)
        svc = FakeService()
        body = json.dumps(
            {"error": {"errors": [{"reason": "quotaExceeded", "message": "secret"}]}}
        ).encode()
        svc.insert_error = HttpError(_Resp(403), body)
        writer = YouTubeWriter(svc)
        with pytest.raises(MigrationError) as ei:
            execute_plan(
                db, plan, writer, plan.id, lambda: ("acct1", plan.inventory_digest)
            )
        assert ei.value.status == 403
        assert ei.value.reason == "quotaExceeded"
        statuses, receipts = _ledger(db, plan.id)
        assert statuses["create_playlist_1"] == "failed"
        assert receipts["create_playlist_1"] == {"status": 403, "reason": "quotaExceeded"}
        assert "secret" not in json.dumps(receipts["create_playlist_1"])
    finally:
        db.close()


def test_failed_no_detail_receipt_stays_null(tmp_path):
    db = seeded_db(tmp_path)
    try:
        plan = save_and(db, default_ops())
        writer = FakeWriter()
        writer.fail_on = {"add_video"}
        with pytest.raises(MigrationError):
            execute_plan(
                db, plan, writer, plan.id, lambda: ("acct1", plan.inventory_digest)
            )
        statuses, receipts = _ledger(db, plan.id)
        assert statuses["add_video_1_1"] == "failed"
        assert receipts["add_video_1_1"] is None
    finally:
        db.close()


# -- malformed/untrusted JSON reason extraction ------------------------------


class _FakeHttpError:
    def __init__(self, content):
        self.content = content


def test_extract_reason_malformed_shapes_are_total():
    cases = [
        b"[1, 2, 3]",
        b'"just a string"',
        b"42",
        b"null",
        b'{"error": "oops"}',
        b'{"error": {"errors": "oops"}}',
        b'{"error": {"errors": {"reason": "quotaExceeded"}}}',
        b'{"error": {"errors": [["quotaExceeded"]]}}',
        b'{"error": {"errors": ["quotaExceeded"]}}',
        b'{"error": {"errors": [{"reason": ["quotaExceeded"]}]}}',
        b'{"error": {"errors": [{"reason": 123}]}}',
        b'{"error": {"errors": [{"reason": {"a": 1}}]}}',
        b'{"error": {"errors": [null]}}',
    ]
    for content in cases:
        assert _extract_reason(_FakeHttpError(content)) is None, content


def test_extract_reason_valid_reason_before_malformed_element():
    content = b'{"error": {"errors": [{"reason": "quotaExceeded"}, "not-a-dict"]}}'
    assert _extract_reason(_FakeHttpError(content)) == "quotaExceeded"


def test_extract_reason_skips_malformed_elements_to_find_safe_reason():
    content = json.dumps(
        {
            "error": {
                "errors": [
                    "not-a-dict",
                    {"reason": ["quotaExceeded"]},
                    {"reason": "forbidden"},
                ]
            }
        }
    ).encode()
    assert _extract_reason(_FakeHttpError(content)) == "forbidden"


def test_malformed_4xx_body_still_definite_not_uncertain():
    svc = FakeService()
    svc.insert_error = HttpError(_Resp(400), b"[1,2,3]")
    writer = YouTubeWriter(svc)
    with pytest.raises(MigrationError) as ei:
        writer.create_playlist("T")
    assert ei.value.status == 400
    assert ei.value.reason is None
    assert "Request rejected by YouTube" in str(ei.value)
