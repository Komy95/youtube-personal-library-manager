"""Planning for reviewable reorganization of the local inventory.

All planning works against the local read-only inventory. No live API access,
no writes to the YouTube account, and no AI inference of categories: targets
come solely from the explicit ``assignments`` mapping the caller supplies.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from youtube_manager.db import Database


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal[
        "create_playlist", "add_video", "remove_item", "rename_playlist"
    ]
    playlist_id: str | None = None
    playlist_ref: str | None = None
    title: str | None = None
    video_id: str | None = None
    item_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    hash: str
    account_id: str
    inventory_digest: str
    operations: list[Operation]
    created_at: str
    description: str = ""


_ALL_FIELDS = ("playlist_id", "playlist_ref", "title", "video_id", "item_id")
_ALLOWED_FIELDS: dict[str, set[str]] = {
    "create_playlist": {"title"},
    "add_video": {"playlist_ref", "playlist_id", "video_id"},
    "remove_item": {"item_id"},
    "rename_playlist": {"playlist_id", "title"},
}
_REQUIRED_FIELDS: dict[str, set[str]] = {
    "create_playlist": {"title"},
    "add_video": {"video_id"},
    "remove_item": {"item_id"},
    "rename_playlist": {"playlist_id", "title"},
}


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _latest_snapshot(db: Database) -> dict:
    row = db._conn.execute(
        "SELECT snapshot_json FROM sync_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise ValueError("no successful snapshot")
    return json.loads(row["snapshot_json"])


def inventory_digest(db: Database) -> str:
    snap = _latest_snapshot(db)
    canonical = {
        "account_id": snap["account_id"],
        "playlists": sorted(snap["playlists"], key=lambda p: p["id"]),
        "videos": sorted(snap["videos"], key=lambda v: v["id"]),
        "items": sorted(snap["items"], key=lambda i: i["id"]),
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _available_video_ids(db: Database) -> set[str]:
    rows = db._conn.execute(
        "SELECT DISTINCT i.video_id, v.data_json "
        "FROM playlist_items i "
        "JOIN playlists p ON p.id = i.playlist_id "
        "JOIN videos v ON v.id = i.video_id "
        "WHERE i.removed_at IS NULL AND p.removed_at IS NULL "
        "AND v.removed_at IS NULL"
    ).fetchall()
    ids = set()
    for r in rows:
        if json.loads(r["data_json"]).get("availability") == "available":
            ids.add(r["video_id"])
    return ids


def _source_items(db: Database) -> dict[str, list[tuple[str, str]]]:
    rows = db._conn.execute(
        "SELECT i.playlist_id, i.id AS item_id, i.video_id "
        "FROM playlist_items i JOIN playlists p ON p.id = i.playlist_id "
        "WHERE i.removed_at IS NULL AND p.removed_at IS NULL "
        "ORDER BY i.playlist_id, i.position, i.id"
    ).fetchall()
    result: dict[str, list[tuple[str, str]]] = {}
    for r in rows:
        result.setdefault(r["playlist_id"], []).append(
            (r["item_id"], r["video_id"])
        )
    return result


def duplicate_report(db: Database) -> dict:
    within: list[dict] = []
    for pid, entries in _source_items(db).items():
        by_video: dict[str, list[str]] = {}
        for item_id, video_id in entries:
            by_video.setdefault(video_id, []).append(item_id)
        for video_id, item_ids in by_video.items():
            if len(item_ids) > 1:
                within.append(
                    {
                        "playlist_id": pid,
                        "video_id": video_id,
                        "item_ids": item_ids,
                    }
                )
    pids = sorted(_source_items(db).keys())
    video_sets = {
        pid: {v for _, v in entries}
        for pid, entries in _source_items(db).items()
    }
    cross: list[dict] = []
    for i in range(len(pids)):
        for j in range(i + 1, len(pids)):
            a, b = pids[i], pids[j]
            shared = sorted(video_sets[a] & video_sets[b])
            if shared:
                cross.append(
                    {
                        "playlist_ids": [a, b],
                        "count": len(shared),
                        "shared_video_ids": shared,
                    }
                )
    return {"within_playlist": within, "cross_playlist": cross}


def _compute_plan_hash(
    account_id: str,
    inventory_digest: str,
    operations: list[Operation],
    description: str,
) -> str:
    payload = {
        "account_id": account_id,
        "inventory_digest": inventory_digest,
        "operations": [op.model_dump(mode="json") for op in operations],
        "description": description,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _verify_plan_hash(plan: Plan) -> None:
    expected = _compute_plan_hash(
        plan.account_id, plan.inventory_digest, plan.operations, plan.description
    )
    if plan.id != expected or plan.hash != expected:
        raise ValueError("plan hash mismatch")


def _validate_operations(operations: list[Operation]) -> None:
    if not operations:
        raise ValueError("operations empty")
    seen: set[str] = set()
    ids: list[str] = []
    create_ids: set[str] = set()
    for op in operations:
        if not op.id or not op.id.strip():
            raise ValueError("empty operation id")
        if op.id in seen:
            raise ValueError(f"duplicate operation id {op.id}")
        seen.add(op.id)
        ids.append(op.id)
        allowed = _ALLOWED_FIELDS[op.kind]
        for f in _ALL_FIELDS:
            val = getattr(op, f)
            if f not in allowed and val is not None:
                raise ValueError(f"{op.kind} {op.id} has irrelevant field {f}")
        for f in _REQUIRED_FIELDS[op.kind]:
            val = getattr(op, f)
            if not val or not str(val).strip():
                raise ValueError(f"{op.kind} {op.id} missing field {f}")
        if op.title is not None and len(op.title.strip()) > 150:
            raise ValueError(f"{op.kind} {op.id} title exceeds 150 characters")
        for dep in op.depends_on:
            if dep not in ids[:-1]:
                raise ValueError(
                    f"operation {op.id} depends on non-earlier {dep}"
                )
        if op.kind == "add_video":
            has_ref = op.playlist_ref is not None
            has_id = op.playlist_id is not None
            if has_ref == has_id:
                raise ValueError(
                    f"add_video {op.id} must have exactly one of "
                    "playlist_ref or playlist_id"
                )
            if has_ref:
                if not str(op.playlist_ref).strip():
                    raise ValueError(f"add_video {op.id} playlist_ref is blank")
                if op.playlist_ref not in create_ids:
                    raise ValueError(f"add_video {op.id} refs unknown create")
                if op.playlist_ref not in op.depends_on:
                    raise ValueError(
                        f"add_video {op.id} missing dependency on {op.playlist_ref}"
                    )
            else:
                if not str(op.playlist_id).strip():
                    raise ValueError(f"add_video {op.id} playlist_id is blank")
        if op.kind == "create_playlist":
            create_ids.add(op.id)


def _owned_playlist_ids(snap: dict) -> set[str]:
    account_id = snap["account_id"]
    return {
        p["id"]
        for p in snap.get("playlists", [])
        if p.get("channel_id") == account_id
    }


def _active_membership_video_ids(db: Database, playlist_id: str) -> set[str]:
    rows = db._conn.execute(
        "SELECT DISTINCT video_id FROM playlist_items "
        "WHERE playlist_id = ? AND removed_at IS NULL",
        (playlist_id,),
    ).fetchall()
    return {r["video_id"] for r in rows}


def build_plan(
    db: Database,
    assignments: dict[str, list[str]],
    deduplicate_source: bool = False,
    existing_targets: dict[str, str] | None = None,
) -> Plan:
    snap = _latest_snapshot(db)
    account_id = snap["account_id"]
    digest = inventory_digest(db)
    if not isinstance(assignments, dict):
        raise ValueError("assignments must be a dict")
    if not assignments:
        raise ValueError("assignments empty")

    targets: list[tuple[str, list[str]]] = []
    assigned: set[str] = set()
    seen_names: set[str] = set()
    for name, vids in assignments.items():
        if not isinstance(vids, list):
            raise ValueError("assignments values must be lists")
        tname = (name or "").strip()
        if not tname:
            raise ValueError("empty target name")
        if len(tname) > 150:
            raise ValueError("target name exceeds 150 characters")
        if tname in seen_names:
            raise ValueError(f"duplicate target name {tname}")
        seen_names.add(tname)
        if not vids:
            raise ValueError(f"empty video list for {tname}")
        for vid in vids:
            if not isinstance(vid, str) or not vid.strip():
                raise ValueError(f"invalid video id in {tname}")
        deduped = list(dict.fromkeys(vids))
        targets.append((tname, deduped))
        assigned.update(deduped)

    available = _available_video_ids(db)
    for _, vids in targets:
        for vid in vids:
            if vid not in available:
                raise ValueError(f"unknown or unavailable video {vid}")

    if existing_targets is None:
        existing_targets = {}
    if not isinstance(existing_targets, dict):
        raise ValueError("existing_targets must be a dict")

    snap_playlists = {p["id"]: p for p in snap.get("playlists", [])}
    active_playlists = _active_playlist_ids(db)
    target_names = {name for name, _ in targets}
    existing_by_name: dict[str, str] = {}
    seen_existing_ids: dict[str, str] = {}
    for raw_name, raw_pid in existing_targets.items():
        if not isinstance(raw_name, str):
            raise ValueError("existing_targets keys must be strings")
        name = raw_name.strip()
        if not name:
            raise ValueError("empty existing target name")
        if name not in target_names:
            raise ValueError(
                f"existing target {raw_name!r} is not an assignment name"
            )
        if name in existing_by_name:
            raise ValueError(f"duplicate existing target for name {name!r}")
        if not isinstance(raw_pid, str) or not raw_pid.strip():
            raise ValueError(f"existing target {raw_name!r} has invalid playlist id")
        pid = raw_pid
        if pid in seen_existing_ids:
            raise ValueError(
                f"existing playlist id {pid!r} aliased by multiple target names"
            )
        if pid not in active_playlists:
            raise ValueError(f"existing target {raw_name!r} playlist {pid!r} is not active")
        pl = snap_playlists.get(pid)
        if pl is None or pl.get("channel_id") != account_id:
            raise ValueError(
                f"existing target {raw_name!r} playlist {pid!r} is not owned by the current account"
            )
        if (pl.get("title") or "") != name:
            raise ValueError(
                f"existing target {raw_name!r} title does not match playlist title"
            )
        seen_existing_ids[pid] = name
        existing_by_name[name] = pid

    operations: list[Operation] = []
    add_by_video: dict[str, list[str]] = {}
    for i, (name, vids) in enumerate(targets):
        pid = existing_by_name.get(name)
        if pid is not None:
            members = _active_membership_video_ids(db, pid)
            remaining = [v for v in vids if v not in members]
            for j, vid in enumerate(remaining):
                add_id = f"add_video_{i + 1}_{j + 1}"
                operations.append(
                    Operation(
                        id=add_id,
                        kind="add_video",
                        playlist_id=pid,
                        video_id=vid,
                    )
                )
                add_by_video.setdefault(vid, []).append(add_id)
            continue
        create_id = f"create_playlist_{i + 1}"
        operations.append(
            Operation(id=create_id, kind="create_playlist", title=name)
        )
        for j, vid in enumerate(vids):
            add_id = f"add_video_{i + 1}_{j + 1}"
            operations.append(
                Operation(
                    id=add_id,
                    kind="add_video",
                    playlist_ref=create_id,
                    video_id=vid,
                    depends_on=[create_id],
                )
            )
            add_by_video.setdefault(vid, []).append(add_id)

    if deduplicate_source:
        counter = 0
        for entries in _source_items(db).values():
            by_video: dict[str, list[str]] = {}
            for item_id, video_id in entries:
                by_video.setdefault(video_id, []).append(item_id)
            for vid, item_ids in by_video.items():
                if len(item_ids) > 1 and vid in assigned and vid in add_by_video:
                    for item_id in item_ids[1:]:
                        counter += 1
                        operations.append(
                            Operation(
                                id=f"remove_item_{counter}",
                                kind="remove_item",
                                item_id=item_id,
                                depends_on=list(add_by_video[vid]),
                            )
                        )

    if not operations:
        raise ValueError("no operations")

    hashval = _compute_plan_hash(account_id, digest, operations, "")
    return Plan(
        id=hashval,
        hash=hashval,
        account_id=account_id,
        inventory_digest=digest,
        operations=operations,
        created_at=_now_utc(),
    )


def _active_item_ids(db: Database) -> set[str]:
    rows = db._conn.execute(
        "SELECT id FROM playlist_items WHERE removed_at IS NULL"
    ).fetchall()
    return {r["id"] for r in rows}


def _active_playlist_ids(db: Database) -> set[str]:
    rows = db._conn.execute(
        "SELECT id FROM playlists WHERE removed_at IS NULL"
    ).fetchall()
    return {r["id"] for r in rows}


def validate_plan(db: Database, plan: Plan) -> None:
    _verify_plan_hash(plan)
    _validate_operations(plan.operations)
    current = _latest_snapshot(db)
    if plan.account_id != current["account_id"]:
        raise ValueError("account changed")
    if plan.inventory_digest != inventory_digest(db):
        raise ValueError("inventory changed")
    active_items = _active_item_ids(db)
    active_playlists = _active_playlist_ids(db)
    owned_playlists = _owned_playlist_ids(current)
    for op in plan.operations:
        if op.kind == "remove_item" and op.item_id not in active_items:
            raise ValueError(f"remove_item unknown item {op.item_id}")
        if op.kind == "rename_playlist" and op.playlist_id not in active_playlists:
            raise ValueError(f"rename_playlist unknown playlist {op.playlist_id}")
        if op.kind == "add_video" and op.playlist_id is not None:
            if op.playlist_id not in active_playlists:
                raise ValueError(
                    f"add_video {op.id} playlist {op.playlist_id} is not active"
                )
            if op.playlist_id not in owned_playlists:
                raise ValueError(
                    f"add_video {op.id} playlist {op.playlist_id} is not owned by the current account"
                )


def save_plan(db: Database, plan: Plan) -> None:
    validate_plan(db, plan)
    conn = db._conn
    row = conn.execute(
        "SELECT data_json FROM migration_plans WHERE id = ?", (plan.id,)
    ).fetchone()
    if row is not None:
        stored = json.loads(row["data_json"])
        incoming = json.loads(plan.model_dump_json())
        stored_body = {k: v for k, v in stored.items() if k != "created_at"}
        incoming_body = {k: v for k, v in incoming.items() if k != "created_at"}
        if stored_body != incoming_body:
            raise ValueError("plan id collision with different content")
        return
    conn.execute(
        "INSERT INTO migration_plans (id, data_json, created_at) VALUES (?, ?, ?)",
        (plan.id, plan.model_dump_json(), plan.created_at),
    )
    conn.commit()


def load_plan(db: Database, plan_id: str) -> Plan:
    row = db._conn.execute(
        "SELECT data_json FROM migration_plans WHERE id = ?", (plan_id,)
    ).fetchone()
    if row is None:
        raise KeyError(plan_id)
    plan = Plan.model_validate(json.loads(row["data_json"]))
    _verify_plan_hash(plan)
    return plan
