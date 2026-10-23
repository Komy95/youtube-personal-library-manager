"""Tests for the workflow CLI groups (content/analysis/plan/migration)."""

from __future__ import annotations

import json
import types
from pathlib import Path

from typer.testing import CliRunner

from youtube_manager import workflow_cli
from youtube_manager.cli import app
from youtube_manager.db import Database
from youtube_manager.models import Playlist, PlaylistItem, Snapshot, Video

runner = CliRunner()


def seed_snapshot(account_id: str = "UC1") -> Snapshot:
    return Snapshot(
        account_id=account_id,
        playlists=[Playlist(id="PL1", title="My List", item_count=1)],
        videos=[Video(id="v1", title="Video One", duration="PT1M")],
        items=[PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0)],
    )


def seed_db(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    with Database(data_dir / "inventory.sqlite3") as db:
        db.apply_snapshot(seed_snapshot())


def invoke(args):
    return runner.invoke(app, args)


def write_transcript(tmp_path: Path, text: str = "Mix 2 cups flour with 1 egg.") -> Path:
    p = tmp_path / "t.txt"
    p.write_text(text, encoding="utf-8")
    return p


def create_plan(data_dir: Path) -> dict:
    result = invoke(
        [
            "--data-dir",
            str(data_dir),
            "plan",
            "create",
            "--assignments",
            json.dumps({"baking": ["v1"]}),
        ]
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_full_offline_workflow(tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)
    transcript = write_transcript(tmp_path)

    result = invoke(
        [
            "--data-dir",
            str(data_dir),
            "content",
            "import",
            "--video-id",
            "v1",
            "--file",
            str(transcript),
        ]
    )
    assert result.exit_code == 0, result.output
    evidence = json.loads(result.output)
    ev_id = evidence["id"]

    result = invoke(["--data-dir", str(data_dir), "content", "list", "--video-id", "v1"])
    assert result.exit_code == 0
    assert len(json.loads(result.output)) == 1

    result = invoke(["--data-dir", str(data_dir), "content", "coverage"])
    assert result.exit_code == 0
    assert json.loads(result.output)["withEvidence"] == 1

    payload = {
        "is_recipe": True,
        "title": {"value": "Flour Cake", "evidence_id": ev_id, "quote": "flour"},
        "ingredients": [
            {"value": "flour", "evidence_id": ev_id, "quote": "flour"},
            {"value": "egg", "evidence_id": ev_id, "quote": "egg"},
        ],
        "steps": [{"value": "mix", "evidence_id": ev_id, "quote": "Mix"}],
        "tags": [{"value": "baking", "evidence_id": ev_id, "quote": "flour"}],
    }
    analysis_file = tmp_path / "analysis.json"
    analysis_file.write_text(json.dumps(payload), encoding="utf-8")

    result = invoke(
        [
            "--data-dir",
            str(data_dir),
            "analysis",
            "import",
            "--video-id",
            "v1",
            "--file",
            str(analysis_file),
        ]
    )
    assert result.exit_code == 0, result.output
    analysis = json.loads(result.output)
    assert analysis["is_recipe"] is True

    result = invoke(["--data-dir", str(data_dir), "analysis", "categories"])
    assert result.exit_code == 0
    categories = json.loads(result.output)
    assert [c["label"] for c in categories] == ["baking"]
    assert categories[0]["proposed"] is True

    plan = create_plan(data_dir)
    plan_id = plan["id"]

    result = invoke(["--data-dir", str(data_dir), "plan", "show", "--plan", plan_id])
    assert result.exit_code == 0
    assert json.loads(result.output)["id"] == plan_id

    result = invoke(["--data-dir", str(data_dir), "plan", "validate", "--plan", plan_id])
    assert result.exit_code == 0
    assert json.loads(result.output)["valid"] is True

    result = invoke(["--data-dir", str(data_dir), "migration", "apply", "--plan", plan_id])
    assert result.exit_code == 0, result.output
    review = json.loads(result.output)
    assert review["status"] == "dry_run"
    assert review["hash"] == plan_id
    assert len(review["operations"]) > 0

    result = invoke(["--data-dir", str(data_dir), "migration", "status", "--plan", plan_id])
    assert result.exit_code == 0
    assert json.loads(result.output)["plan_id"] == plan_id


def test_offline_commands_never_touch_auth(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)
    write_transcript(tmp_path)

    def boom(*args, **kwargs):
        raise AssertionError("auth/network accessed")

    monkeypatch.setattr("youtube_manager.cli.auth_module.load_credentials", boom)
    monkeypatch.setattr(workflow_cli, "make_write_service", boom)
    monkeypatch.setattr(workflow_cli, "remote_preflight", boom)

    for args in (["content", "list"], ["analysis", "list"], ["plan", "duplicates"]):
        result = invoke(["--data-dir", str(data_dir), *args])
        assert result.exit_code == 0, (args, result.output)

    result = invoke(
        ["--data-dir", str(data_dir), "migration", "status", "--plan", "nope"]
    )
    assert result.exit_code == 0


def test_dryrun_ignores_approval(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)
    plan_id = create_plan(data_dir)["id"]

    def boom(*args, **kwargs):
        raise AssertionError("token/API accessed")

    fake_auth = types.SimpleNamespace(load_write_credentials=boom)
    monkeypatch.setattr(workflow_cli, "write_auth", fake_auth)
    monkeypatch.setattr(workflow_cli, "make_write_service", boom)
    monkeypatch.setattr(workflow_cli, "remote_preflight", boom)

    result = invoke(
        [
            "--data-dir",
            str(data_dir),
            "migration",
            "apply",
            "--plan",
            plan_id,
            "--approve-plan",
            plan_id,
        ]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "dry_run"


def test_execute_wrong_or_missing_approval_fails_before_tokens(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)
    plan_id = create_plan(data_dir)["id"]

    def boom(*args, **kwargs):
        raise AssertionError("token/API/browser accessed")

    fake_auth = types.SimpleNamespace(load_write_credentials=boom, authenticate_write=boom)
    monkeypatch.setattr(workflow_cli, "write_auth", fake_auth)
    monkeypatch.setattr(workflow_cli, "make_write_service", boom)
    monkeypatch.setattr(workflow_cli, "remote_preflight", boom)

    result = invoke(
        [
            "--data-dir",
            str(data_dir),
            "migration",
            "apply",
            "--plan",
            plan_id,
            "--execute",
            "--approve-plan",
            "wrong",
        ]
    )
    assert result.exit_code != 0
    assert "approval" in result.output.lower()

    result = invoke(
        ["--data-dir", str(data_dir), "migration", "apply", "--plan", plan_id, "--execute"]
    )
    assert result.exit_code != 0
    assert "approval" in result.output.lower()


def test_execute_invokes_preflight_and_verifies_account(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)
    plan = create_plan(data_dir)
    plan_id = plan["id"]
    plan_account = plan["account_id"]
    plan_digest = plan["inventory_digest"]

    preflight_calls = []

    def fake_remote_preflight(service):
        preflight_calls.append(service)
        return (plan_account, plan_digest)

    monkeypatch.setattr(workflow_cli, "remote_preflight", fake_remote_preflight)

    service_obj = object()
    monkeypatch.setattr(workflow_cli, "make_write_service", lambda creds: service_obj)

    fake_auth = types.SimpleNamespace(load_write_credentials=lambda s, h: object())
    monkeypatch.setattr(workflow_cli, "write_auth", fake_auth)

    executed = {}

    def fake_execute_plan(db, plan_obj, writer, approval, preflight):
        executed["approval"] = approval
        executed["preflight"] = preflight()
        return {"status": "executed", "hash": plan_obj.hash}

    fake_migration = types.SimpleNamespace(
        YouTubeWriter=lambda s: object(),
        execute_plan=fake_execute_plan,
    )
    monkeypatch.setattr(workflow_cli, "migration", fake_migration)

    result = invoke(
        [
            "--data-dir",
            str(data_dir),
            "migration",
            "apply",
            "--plan",
            plan_id,
            "--execute",
            "--approve-plan",
            plan_id,
        ]
    )
    assert result.exit_code == 0, result.output
    assert preflight_calls == [service_obj]
    assert executed["approval"] == plan_id
    assert executed["preflight"] == (plan_account, plan_digest)
    assert json.loads(result.output)["status"] == "executed"


def test_execute_verifies_plan_account(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)
    plan = create_plan(data_dir)
    plan_id = plan["id"]
    plan_digest = plan["inventory_digest"]

    def fake_remote_preflight(service):
        return ("OTHER_ACCOUNT", plan_digest)

    monkeypatch.setattr(workflow_cli, "remote_preflight", fake_remote_preflight)
    monkeypatch.setattr(workflow_cli, "make_write_service", lambda creds: object())

    fake_auth = types.SimpleNamespace(load_write_credentials=lambda s, h: object())
    monkeypatch.setattr(workflow_cli, "write_auth", fake_auth)

    def fake_execute_plan(db, plan_obj, writer, approval, preflight):
        preflight()

    fake_migration = types.SimpleNamespace(
        YouTubeWriter=lambda s: object(),
        execute_plan=fake_execute_plan,
    )
    monkeypatch.setattr(workflow_cli, "migration", fake_migration)

    result = invoke(
        [
            "--data-dir",
            str(data_dir),
            "migration",
            "apply",
            "--plan",
            plan_id,
            "--execute",
            "--approve-plan",
            plan_id,
        ]
    )
    assert result.exit_code != 0
    assert "account" in result.output.lower()


def test_migration_auth_verifies_account(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)
    plan_id = create_plan(data_dir)["id"]

    auth_calls = []

    def fake_authenticate_write(settings, path, approved_plan):
        auth_calls.append((settings, path, approved_plan))
        return object()

    fake_auth = types.SimpleNamespace(authenticate_write=fake_authenticate_write)
    monkeypatch.setattr(workflow_cli, "write_auth", fake_auth)

    class FakeService:
        def channels(self):
            return self

        def list(self, **kwargs):
            return self

        def execute(self):
            return {"items": [{"id": "UC1", "snippet": {"title": "Test Channel"}}]}

    monkeypatch.setattr(workflow_cli, "make_write_service", lambda creds: FakeService())

    secrets = tmp_path / "client.json"
    secrets.write_text("{}", encoding="utf-8")

    result = invoke(
        [
            "--data-dir",
            str(data_dir),
            "migration",
            "auth",
            "--plan",
            plan_id,
            "--approve-plan",
            plan_id,
            "--client-secrets",
            str(secrets),
        ]
    )
    assert result.exit_code == 0, result.output
    assert auth_calls[0][1] == secrets
    assert auth_calls[0][2] == plan_id
    out = json.loads(result.output)
    assert out["authenticated"] is True
    assert out["account"]["id"] == "UC1"


def test_migration_auth_wrong_approval_no_browser(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)
    plan_id = create_plan(data_dir)["id"]

    def boom(*args, **kwargs):
        raise AssertionError("browser/API accessed")

    fake_auth = types.SimpleNamespace(authenticate_write=boom)
    monkeypatch.setattr(workflow_cli, "write_auth", fake_auth)
    monkeypatch.setattr(workflow_cli, "make_write_service", boom)

    secrets = tmp_path / "client.json"
    secrets.write_text("{}", encoding="utf-8")

    result = invoke(
        [
            "--data-dir",
            str(data_dir),
            "migration",
            "auth",
            "--plan",
            plan_id,
            "--approve-plan",
            "wrong",
            "--client-secrets",
            str(secrets),
        ]
    )
    assert result.exit_code != 0
    assert "approval" in result.output.lower()


def test_plan_duplicates_output_file(tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)
    out = tmp_path / "dupes.json"

    result = invoke(
        ["--data-dir", str(data_dir), "plan", "duplicates", "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data) == {"within_playlist", "cross_playlist"}


def test_execute_completed_noop_skips_auth_and_validation(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)
    plan = create_plan(data_dir)
    plan_id = plan["id"]

    with Database(data_dir / "inventory.sqlite3") as db:
        conn = db._conn
        conn.execute(
            "INSERT INTO migration_operations "
            "(plan_id, operation_id, status, receipt_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (plan_id, "__run__", "completed", "null", "2026-01-01T00:00:00+00:00"),
        )
        for op in plan["operations"]:
            if op["kind"] == "create_playlist":
                receipt = {"playlist_id": "NEWPL"}
            elif op["kind"] == "add_video":
                receipt = {"item_id": "NEWITEM"}
            else:
                receipt = {}
            conn.execute(
                "INSERT INTO migration_operations "
                "(plan_id, operation_id, status, receipt_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    plan_id,
                    op["id"],
                    "completed",
                    json.dumps(receipt),
                    "2026-01-01T00:00:00+00:00",
                ),
            )
        conn.commit()

    drifted = Snapshot(
        account_id="UC1",
        playlists=[Playlist(id="PL1", title="My List", item_count=1)],
        videos=[
            Video(id="v1", title="Video One", duration="PT1M"),
            Video(id="v2", title="Video Two", duration="PT1M"),
        ],
        items=[PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0)],
    )
    with Database(data_dir / "inventory.sqlite3") as db:
        db.apply_snapshot(drifted)

    def boom(*args, **kwargs):
        raise AssertionError("auth/network accessed")

    fake_auth = types.SimpleNamespace(
        load_write_credentials=boom, authenticate_write=boom
    )
    monkeypatch.setattr(workflow_cli, "write_auth", fake_auth)
    monkeypatch.setattr(workflow_cli, "make_write_service", boom)
    monkeypatch.setattr(workflow_cli, "remote_preflight", boom)

    result = invoke(
        [
            "--data-dir",
            str(data_dir),
            "migration",
            "apply",
            "--plan",
            plan_id,
            "--execute",
            "--approve-plan",
            plan_id,
        ]
    )
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["status"] == "completed_noop"
    assert set(out["receipts"]) == {op["id"] for op in plan["operations"]}

    result = invoke(
        [
            "--data-dir",
            str(data_dir),
            "migration",
            "apply",
            "--plan",
            plan_id,
            "--execute",
            "--approve-plan",
            "wrong",
        ]
    )
    assert result.exit_code != 0
    assert "approval" in result.output.lower()


def test_execute_write_uncertain_prints_concise_no_traceback(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)
    plan = create_plan(data_dir)
    plan_id = plan["id"]

    fake_auth = types.SimpleNamespace(load_write_credentials=lambda s, h: object())
    monkeypatch.setattr(workflow_cli, "write_auth", fake_auth)
    monkeypatch.setattr(workflow_cli, "make_write_service", lambda creds: object())

    from youtube_manager.migration import WriteUncertainError

    def fake_execute_plan(db, plan_obj, writer, approval, preflight):
        raise WriteUncertainError("Network error.")

    fake_migration = types.SimpleNamespace(
        YouTubeWriter=lambda s: object(),
        execute_plan=fake_execute_plan,
    )
    monkeypatch.setattr(workflow_cli, "migration", fake_migration)

    result = invoke(
        [
            "--data-dir",
            str(data_dir),
            "migration",
            "apply",
            "--plan",
            plan_id,
            "--execute",
            "--approve-plan",
            plan_id,
        ]
    )
    assert result.exit_code != 0
    assert "Network error." in result.output
    assert "Traceback" not in result.output


def test_plan_create_assignments_file(tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)
    af = tmp_path / "assignments.json"
    af.write_text(json.dumps({"baking": ["v1"]}), encoding="utf-8")

    result = invoke(
        ["--data-dir", str(data_dir), "plan", "create", "--assignments-file", str(af)]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["id"]


def test_plan_create_assignments_missing_and_both(tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)

    result = invoke(["--data-dir", str(data_dir), "plan", "create"])
    assert result.exit_code != 0
    assert "exactly one" in result.output.lower()

    af = tmp_path / "assignments.json"
    af.write_text(json.dumps({"baking": ["v1"]}), encoding="utf-8")
    result = invoke(
        [
            "--data-dir",
            str(data_dir),
            "plan",
            "create",
            "--assignments",
            json.dumps({"baking": ["v1"]}),
            "--assignments-file",
            str(af),
        ]
    )
    assert result.exit_code != 0
    assert "exactly one" in result.output.lower()


def test_plan_create_assignments_file_nonobject_and_oversize(tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)

    lst = tmp_path / "list.json"
    lst.write_text(json.dumps(["v1"]), encoding="utf-8")
    result = invoke(
        ["--data-dir", str(data_dir), "plan", "create", "--assignments-file", str(lst)]
    )
    assert result.exit_code != 0
    assert "object" in result.output.lower()

    big = tmp_path / "big.json"
    big.write_text("x" * (2 * 1024 * 1024 + 10), encoding="utf-8")
    result = invoke(
        ["--data-dir", str(data_dir), "plan", "create", "--assignments-file", str(big)]
    )
    assert result.exit_code != 0
    assert "too large" in result.output.lower()


def test_plan_create_assignments_file_read_error(tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)

    missing = tmp_path / "missing.json"
    result = invoke(
        [
            "--data-dir",
            str(data_dir),
            "plan",
            "create",
            "--assignments-file",
            str(missing),
        ]
    )
    assert result.exit_code != 0
    assert "file system" in result.output.lower()

    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"\xff\xfe\x00\x01")
    result = invoke(
        ["--data-dir", str(data_dir), "plan", "create", "--assignments-file", str(bad)]
    )
    assert result.exit_code != 0
    assert "utf-8" in result.output.lower()


# -- existing targets CLI ----------------------------------------------------


def seed_existing_snapshot(account_id: str = "UC1") -> Snapshot:
    return Snapshot(
        account_id=account_id,
        playlists=[
            Playlist(id="PL1", title="baking", channel_id=account_id, item_count=1),
            Playlist(id="PL2", title="src", channel_id=account_id, item_count=1),
        ],
        videos=[
            Video(id="v1", title="Video One", duration="PT1M"),
            Video(id="v2", title="Video Two", duration="PT1M"),
        ],
        items=[
            PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0),
            PlaylistItem(id="i2", playlist_id="PL2", video_id="v2", position=0),
        ],
    )


def test_plan_create_existing_targets_file(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    with Database(data_dir / "inventory.sqlite3") as db:
        db.apply_snapshot(seed_existing_snapshot())

    et = tmp_path / "existing.json"
    et.write_text(json.dumps({"baking": "PL1"}), encoding="utf-8")

    result = invoke(
        [
            "--data-dir",
            str(data_dir),
            "plan",
            "create",
            "--assignments",
            json.dumps({"baking": ["v1", "v2"]}),
            "--existing-targets-file",
            str(et),
        ]
    )
    assert result.exit_code == 0, result.output
    plan = json.loads(result.output)
    ops = plan["operations"]
    assert all(o["kind"] != "create_playlist" for o in ops)
    adds = [o for o in ops if o["kind"] == "add_video"]
    assert len(adds) == 1
    assert adds[0]["playlist_id"] == "PL1"
    assert adds[0]["video_id"] == "v2"


def test_plan_create_existing_targets_file_bad_types(tmp_path):
    data_dir = tmp_path / "data"
    seed_db(data_dir)

    lst = tmp_path / "list.json"
    lst.write_text(json.dumps(["PL1"]), encoding="utf-8")
    result = invoke(
        [
            "--data-dir",
            str(data_dir),
            "plan",
            "create",
            "--assignments",
            json.dumps({"baking": ["v1"]}),
            "--existing-targets-file",
            str(lst),
        ]
    )
    assert result.exit_code != 0
    assert "object" in result.output.lower()

    badval = tmp_path / "badval.json"
    badval.write_text(json.dumps({"baking": 123}), encoding="utf-8")
    result = invoke(
        [
            "--data-dir",
            str(data_dir),
            "plan",
            "create",
            "--assignments",
            json.dumps({"baking": ["v1"]}),
            "--existing-targets-file",
            str(badval),
        ]
    )
    assert result.exit_code != 0
    assert "string playlist" in result.output.lower()


def test_analysis_run_removed(tmp_path, monkeypatch):
    import socket

    data_dir = tmp_path / "unused"
    result = runner.invoke(
        app,
        ["--data-dir", str(data_dir), "analysis", "--help"],
        color=False,
    )
    assert result.exit_code == 0, result.output
    commands = {command.name for command in workflow_cli.analysis_app.registered_commands}
    assert {"import", "list", "categories"} <= commands
    assert "run" not in commands

    def forbidden(*args, **kwargs):
        raise AssertionError("removed command must not access the database or network")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr("youtube_manager.db.Database", forbidden)
    result = runner.invoke(
        app,
        ["--data-dir", str(data_dir), "analysis", "run", "--video-id", "example", "--model", "example"],
        color=False,
    )
    assert result.exit_code == 2, result.output
    assert not data_dir.exists()
