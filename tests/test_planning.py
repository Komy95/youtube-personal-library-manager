"""Tests for the planning layer."""

from __future__ import annotations

import pytest

from youtube_manager.db import Database
from youtube_manager.models import Availability, Playlist, PlaylistItem, Snapshot, Video
from youtube_manager.planning import (
    Operation,
    Plan,
    _compute_plan_hash,
    _validate_operations,
    build_plan,
    duplicate_report,
    inventory_digest,
    load_plan,
    save_plan,
    validate_plan,
)


def make_snapshot(
    account_id="acct1",
    playlists=None,
    videos=None,
    items=None,
    captured_at="2026-01-01T00:00:00+00:00",
):
    return Snapshot(
        account_id=account_id,
        playlists=playlists
        if playlists is not None
        else [Playlist(id="PL1", title="List", item_count=2)],
        videos=videos
        if videos is not None
        else [
            Video(id="v1", title="One"),
            Video(id="v2", title="Two"),
        ],
        items=items
        if items is not None
        else [
            PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0),
            PlaylistItem(id="i2", playlist_id="PL1", video_id="v2", position=1),
        ],
        captured_at=captured_at,
    )


def make_db(tmp_path, snapshot=None):
    db = Database(tmp_path / "inventory.sqlite3")
    db.apply_snapshot(snapshot or make_snapshot())
    return db


def make_plan(db, operations, account_id="acct1", description=""):
    digest = inventory_digest(db)
    h = _compute_plan_hash(account_id, digest, operations, description)
    return Plan(
        id=h,
        hash=h,
        account_id=account_id,
        inventory_digest=digest,
        operations=operations,
        created_at="2026-01-01T00:00:00+00:00",
        description=description,
    )


def test_inventory_digest_stable_across_captured_at(tmp_path):
    db = make_db(tmp_path, make_snapshot(captured_at="2026-01-01T00:00:00+00:00"))
    try:
        d1 = inventory_digest(db)
        db.apply_snapshot(make_snapshot(captured_at="2026-01-02T12:00:00+00:00"))
        d2 = inventory_digest(db)
        assert d1 == d2
        assert len(d1) == 64
    finally:
        db.close()


def test_inventory_digest_requires_snapshot(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    try:
        with pytest.raises(ValueError):
            inventory_digest(db)
    finally:
        db.close()


def test_inventory_digest_changes_on_position_and_account(tmp_path):
    db = make_db(tmp_path)
    try:
        d1 = inventory_digest(db)
        reordered = make_snapshot(
            items=[
                PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=1),
                PlaylistItem(id="i2", playlist_id="PL1", video_id="v2", position=0),
            ]
        )
        db.apply_snapshot(reordered)
        assert inventory_digest(db) != d1

        other_account = make_snapshot(
            account_id="acct2",
            playlists=[],
            videos=[],
            items=[],
        )
        db2 = Database(tmp_path / "other.sqlite3")
        try:
            db2.apply_snapshot(other_account)
            assert inventory_digest(db2) != d1
        finally:
            db2.close()
    finally:
        db.close()


def test_duplicate_report_within_and_cross(tmp_path):
    playlists = [
        Playlist(id="PL1", title="A"),
        Playlist(id="PL2", title="B"),
    ]
    videos = [Video(id="v1", title="V1"), Video(id="v2", title="V2")]
    items = [
        PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0),
        PlaylistItem(id="i2", playlist_id="PL1", video_id="v1", position=1),
        PlaylistItem(id="i3", playlist_id="PL1", video_id="v2", position=2),
        PlaylistItem(id="i4", playlist_id="PL2", video_id="v1", position=0),
    ]
    db = make_db(
        tmp_path,
        make_snapshot(playlists=playlists, videos=videos, items=items),
    )
    try:
        report = duplicate_report(db)
        within = {w["playlist_id"]: w for w in report["within_playlist"]}
        assert within["PL1"]["video_id"] == "v1"
        assert within["PL1"]["item_ids"] == ["i1", "i2"]
        cross = report["cross_playlist"]
        assert len(cross) == 1
        assert cross[0]["playlist_ids"] == ["PL1", "PL2"]
        assert cross[0]["count"] == 1
        assert cross[0]["shared_video_ids"] == ["v1"]
    finally:
        db.close()


def test_build_plan_validation_unknown_unavailable_empty(tmp_path):
    db = make_db(tmp_path)
    try:
        with pytest.raises(ValueError):
            build_plan(db, {"Cat": ["missing"]})
        with pytest.raises(ValueError):
            build_plan(db, {"Cat": []})
        with pytest.raises(ValueError):
            build_plan(db, {"   ": ["v1"]})
        with pytest.raises(ValueError):
            build_plan(db, {"x" * 151: ["v1"]})
        with pytest.raises(ValueError):
            build_plan(db, {})
    finally:
        db.close()


def test_build_plan_unavailable_video_rejected(tmp_path):
    db = make_db(
        tmp_path,
        make_snapshot(
            playlists=[Playlist(id="PL1", title="A")],
            videos=[Video(id="v1", availability=Availability.private)],
            items=[PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0)],
        ),
    )
    try:
        with pytest.raises(ValueError):
            build_plan(db, {"Cat": ["v1"]})
    finally:
        db.close()


def test_build_plan_deterministic_and_structure(tmp_path):
    db = make_db(tmp_path)
    try:
        p1 = build_plan(db, {"Cat A": ["v1", "v2"], "Cat B": ["v1"]})
        p2 = build_plan(db, {"Cat A": ["v1", "v2"], "Cat B": ["v1"]})
        assert p1.hash == p2.hash
        assert p1.id == p2.id
        assert p1.hash == p1.id
        assert p1.account_id == "acct1"
        assert p1.inventory_digest == inventory_digest(db)

        creates = [o for o in p1.operations if o.kind == "create_playlist"]
        assert [c.title for c in creates] == ["Cat A", "Cat B"]
        adds = [o for o in p1.operations if o.kind == "add_video"]
        assert [a.video_id for a in adds] == ["v1", "v2", "v1"]
        assert adds[0].playlist_ref == creates[0].id
        assert adds[0].depends_on == [creates[0].id]
        assert adds[2].playlist_ref == creates[1].id
    finally:
        db.close()


def test_build_plan_dedup_target_preserves_order(tmp_path):
    db = make_db(tmp_path)
    try:
        plan = build_plan(db, {"Cat": ["v2", "v1", "v2"]})
        adds = [o for o in plan.operations if o.kind == "add_video"]
        assert [a.video_id for a in adds] == ["v2", "v1"]
    finally:
        db.close()


def test_build_plan_deduplicate_source(tmp_path):
    playlists = [Playlist(id="PL1", title="A")]
    videos = [Video(id="v1", title="V1"), Video(id="v2", title="V2")]
    items = [
        PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0),
        PlaylistItem(id="i2", playlist_id="PL1", video_id="v1", position=1),
        PlaylistItem(id="i3", playlist_id="PL1", video_id="v2", position=2),
    ]
    db = make_db(
        tmp_path,
        make_snapshot(playlists=playlists, videos=videos, items=items),
    )
    try:
        plan = build_plan(db, {"Cat": ["v1"]}, deduplicate_source=True)
        removes = [o for o in plan.operations if o.kind == "remove_item"]
        assert [r.item_id for r in removes] == ["i2"]
        add_ids = [o.id for o in plan.operations if o.kind == "add_video"]
        assert removes[0].depends_on == add_ids
    finally:
        db.close()


def test_build_plan_dedup_skips_unassigned(tmp_path):
    playlists = [Playlist(id="PL1", title="A")]
    videos = [Video(id="v1", title="V1"), Video(id="v2", title="V2")]
    items = [
        PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0),
        PlaylistItem(id="i2", playlist_id="PL1", video_id="v1", position=1),
        PlaylistItem(id="i3", playlist_id="PL1", video_id="v2", position=2),
    ]
    db = make_db(
        tmp_path,
        make_snapshot(playlists=playlists, videos=videos, items=items),
    )
    try:
        plan = build_plan(db, {"Cat": ["v2"]}, deduplicate_source=True)
        assert all(o.kind != "remove_item" for o in plan.operations)
    finally:
        db.close()


def test_validate_operations_rejects_invalid(tmp_path):
    db = make_db(tmp_path)
    try:
        dup = [
            Operation(id="c1", kind="create_playlist", title="A"),
            Operation(id="c1", kind="create_playlist", title="B"),
        ]
        with pytest.raises(ValueError):
            validate_plan(
                db,
                Plan(
                    id="x",
                    hash="x",
                    account_id="acct1",
                    inventory_digest=inventory_digest(db),
                    operations=dup,
                    created_at="2026-01-01T00:00:00+00:00",
                ),
            )

        bad_ref = [
            Operation(id="a1", kind="add_video", playlist_ref="c1", video_id="v1"),
        ]
        with pytest.raises(ValueError):
            validate_plan(
                db,
                Plan(
                    id="x",
                    hash="x",
                    account_id="acct1",
                    inventory_digest=inventory_digest(db),
                    operations=bad_ref,
                    created_at="2026-01-01T00:00:00+00:00",
                ),
            )

        bad_field = [Operation(id="c1", kind="create_playlist", title="A", video_id="v1")]
        with pytest.raises(ValueError):
            validate_plan(
                db,
                Plan(
                    id="x",
                    hash="x",
                    account_id="acct1",
                    inventory_digest=inventory_digest(db),
                    operations=bad_field,
                    created_at="2026-01-01T00:00:00+00:00",
                ),
            )

        missing = [Operation(id="c1", kind="create_playlist")]
        with pytest.raises(ValueError):
            validate_plan(
                db,
                Plan(
                    id="x",
                    hash="x",
                    account_id="acct1",
                    inventory_digest=inventory_digest(db),
                    operations=missing,
                    created_at="2026-01-01T00:00:00+00:00",
                ),
            )
    finally:
        db.close()


def test_save_and_load_plan(tmp_path):
    db = make_db(tmp_path)
    try:
        plan = build_plan(db, {"Cat": ["v1"]})
        save_plan(db, plan)
        save_plan(db, plan)
        loaded = load_plan(db, plan.id)
        assert loaded.hash == plan.hash
        assert loaded == plan
        with pytest.raises(KeyError):
            load_plan(db, "nonexistent")
    finally:
        db.close()


def test_save_plan_rejects_tamper(tmp_path):
    db = make_db(tmp_path)
    try:
        plan = build_plan(db, {"Cat": ["v1"]})
        tampered = plan.model_copy(update={"hash": "0" * 64})
        with pytest.raises(ValueError):
            save_plan(db, tampered)
    finally:
        db.close()


def test_validate_plan_detects_drift(tmp_path):
    db = make_db(tmp_path)
    try:
        plan = build_plan(db, {"Cat": ["v1"]})
        validate_plan(db, plan)

        db.apply_snapshot(make_snapshot(playlists=[], videos=[], items=[]))
        with pytest.raises(ValueError):
            validate_plan(db, plan)
    finally:
        db.close()


def test_validate_plan_detects_account_change(tmp_path):
    db = make_db(tmp_path)
    try:
        plan = build_plan(db, {"Cat": ["v1"]})
        db2 = Database(tmp_path / "other.sqlite3")
        try:
            db2.apply_snapshot(make_snapshot(account_id="acct2"))
            with pytest.raises(ValueError):
                validate_plan(db2, plan)
        finally:
            db2.close()
    finally:
        db.close()


def test_save_plan_rejects_tampered_id(tmp_path):
    db = make_db(tmp_path)
    try:
        plan = build_plan(db, {"Cat": ["v1"]})
        tampered = plan.model_copy(update={"id": "0" * 64})
        with pytest.raises(ValueError):
            save_plan(db, tampered)
    finally:
        db.close()


def test_validate_plan_rejects_empty_operations(tmp_path):
    db = make_db(tmp_path)
    try:
        plan = make_plan(db, [])
        with pytest.raises(ValueError):
            validate_plan(db, plan)
    finally:
        db.close()


def test_validate_plan_rejects_empty_operation_id(tmp_path):
    db = make_db(tmp_path)
    try:
        plan = make_plan(db, [Operation(id="", kind="create_playlist", title="A")])
        with pytest.raises(ValueError):
            validate_plan(db, plan)
    finally:
        db.close()


def test_build_plan_rejects_stale_orphan_video(tmp_path):
    db = make_db(
        tmp_path,
        make_snapshot(
            playlists=[Playlist(id="PL1", title="A")],
            videos=[Video(id="v1", title="One"), Video(id="v2", title="Two")],
            items=[PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0)],
        ),
    )
    try:
        with pytest.raises(ValueError):
            build_plan(db, {"Cat": ["v2"]})
    finally:
        db.close()


def test_validate_rejects_add_video_missing_dependency(tmp_path):
    db = make_db(tmp_path)
    try:
        ops = [
            Operation(id="c1", kind="create_playlist", title="A"),
            Operation(id="a1", kind="add_video", playlist_ref="c1", video_id="v1"),
        ]
        plan = make_plan(db, ops)
        with pytest.raises(ValueError):
            validate_plan(db, plan)
    finally:
        db.close()


def test_build_plan_rejects_wrong_assignments_type(tmp_path):
    db = make_db(tmp_path)
    try:
        with pytest.raises(ValueError):
            build_plan(db, [("Cat", ["v1"])])
        with pytest.raises(ValueError):
            build_plan(db, {"Cat": "v1"})
    finally:
        db.close()


def test_build_plan_rejects_malformed_list_shapes(tmp_path):
    db = make_db(tmp_path)
    try:
        with pytest.raises(ValueError):
            build_plan(db, {"Cat": ["v1", {"nested": "json"}]})
        with pytest.raises(ValueError):
            build_plan(db, {"Cat": ["v1", ["v2"]]})
        with pytest.raises(ValueError):
            build_plan(db, {"Cat": ["v1", ("v2", "v3")]})
    finally:
        db.close()


def test_build_plan_rejects_non_unique_normalized_names(tmp_path):
    db = make_db(tmp_path)
    try:
        with pytest.raises(ValueError):
            build_plan(db, {"Cat": ["v1"], "  Cat  ": ["v2"]})
    finally:
        db.close()


def test_build_plan_rejects_invalid_video_id_string(tmp_path):
    db = make_db(tmp_path)
    try:
        with pytest.raises(ValueError):
            build_plan(db, {"Cat": ["v1", ""]})
        with pytest.raises(ValueError):
            build_plan(db, {"Cat": ["v1", 123]})
    finally:
        db.close()


def test_validate_rejects_unknown_remove_and_rename(tmp_path):
    db = make_db(tmp_path)
    try:
        remove_ops = [Operation(id="r1", kind="remove_item", item_id="nope")]
        with pytest.raises(ValueError):
            validate_plan(db, make_plan(db, remove_ops))

        rename_ops = [
            Operation(id="rn1", kind="rename_playlist", playlist_id="nope", title="X")
        ]
        with pytest.raises(ValueError):
            validate_plan(db, make_plan(db, rename_ops))
    finally:
        db.close()


def test_save_plan_idempotent_across_timestamps(tmp_path):
    db = make_db(tmp_path)
    try:
        p1 = build_plan(db, {"Cat": ["v1"]})
        save_plan(db, p1)

        p2 = build_plan(db, {"Cat": ["v1"]})
        p2 = p2.model_copy(update={"created_at": "2030-01-01T00:00:00+00:00"})
        assert p2.id == p1.id
        assert p2.hash == p1.hash
        assert p2.created_at != p1.created_at
        save_plan(db, p2)

        loaded = load_plan(db, p1.id)
        assert loaded.created_at == p1.created_at
    finally:
        db.close()


# -- existing targets --------------------------------------------------------


def make_existing_snapshot():
    return Snapshot(
        account_id="acct1",
        playlists=[
            Playlist(id="PL1", title="Existing", channel_id="acct1", item_count=1),
            Playlist(id="PL2", title="Source", channel_id="acct1", item_count=1),
        ],
        videos=[Video(id="v1", title="One"), Video(id="v2", title="Two")],
        items=[
            PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0),
            PlaylistItem(id="i2", playlist_id="PL2", video_id="v2", position=0),
        ],
    )


def test_build_plan_existing_target_no_create_and_skips_existing(tmp_path):
    db = make_db(tmp_path, make_existing_snapshot())
    try:
        plan = build_plan(
            db,
            {"Existing": ["v1", "v2"]},
            existing_targets={"Existing": "PL1"},
        )
        creates = [o for o in plan.operations if o.kind == "create_playlist"]
        assert creates == []
        adds = [o for o in plan.operations if o.kind == "add_video"]
        assert len(adds) == 1
        assert adds[0].playlist_id == "PL1"
        assert adds[0].playlist_ref is None
        assert adds[0].video_id == "v2"
    finally:
        db.close()


def test_build_plan_existing_target_preserves_cross_playlist(tmp_path):
    db = make_db(tmp_path, make_existing_snapshot())
    try:
        plan = build_plan(
            db,
            {"Existing": ["v1", "v2"], "New": ["v1"]},
            existing_targets={"Existing": "PL1"},
        )
        creates = [o for o in plan.operations if o.kind == "create_playlist"]
        assert [c.title for c in creates] == ["New"]
        adds = [o for o in plan.operations if o.kind == "add_video"]
        by_video = {a.video_id: a for a in adds}
        assert by_video["v2"].playlist_id == "PL1"
        assert by_video["v2"].playlist_ref is None
        assert by_video["v1"].playlist_ref == creates[0].id
    finally:
        db.close()


def test_build_plan_defaults_unchanged_when_option_omitted(tmp_path):
    db = make_db(tmp_path)
    try:
        p1 = build_plan(db, {"Cat": ["v1", "v2"]})
        p2 = build_plan(db, {"Cat": ["v1", "v2"]}, existing_targets=None)
        assert p1.hash == p2.hash
        assert p1.id == p2.id
    finally:
        db.close()


def test_build_plan_existing_target_unknown_key_rejected(tmp_path):
    db = make_db(tmp_path, make_existing_snapshot())
    try:
        with pytest.raises(ValueError):
            build_plan(db, {"Existing": ["v1"]}, existing_targets={"Nope": "PL1"})
    finally:
        db.close()


def test_build_plan_existing_target_title_mismatch_rejected(tmp_path):
    snap = Snapshot(
        account_id="acct1",
        playlists=[Playlist(id="PL1", title="Actual", channel_id="acct1", item_count=1)],
        videos=[Video(id="v1", title="One")],
        items=[PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0)],
    )
    db = make_db(tmp_path, snap)
    try:
        with pytest.raises(ValueError):
            build_plan(db, {"Wanted": ["v1"]}, existing_targets={"Wanted": "PL1"})
    finally:
        db.close()


def test_build_plan_existing_target_foreign_rejected(tmp_path):
    snap = Snapshot(
        account_id="acct1",
        playlists=[
            Playlist(id="PL1", title="Mine", channel_id="acct1", item_count=1),
            Playlist(id="PLF", title="Foreign", channel_id="other", item_count=0),
        ],
        videos=[Video(id="v1", title="One")],
        items=[PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0)],
    )
    db = make_db(tmp_path, snap)
    try:
        with pytest.raises(ValueError):
            build_plan(db, {"Foreign": ["v1"]}, existing_targets={"Foreign": "PLF"})
    finally:
        db.close()


def test_build_plan_existing_target_removed_rejected(tmp_path):
    db = make_db(tmp_path, make_existing_snapshot())
    try:
        with pytest.raises(ValueError):
            build_plan(
                db,
                {"Existing": ["v1"]},
                existing_targets={"Existing": "PL_MISSING"},
            )
    finally:
        db.close()


def test_build_plan_existing_target_repeated_id_rejected(tmp_path):
    snap = Snapshot(
        account_id="acct1",
        playlists=[Playlist(id="PL1", title="A", channel_id="acct1", item_count=1)],
        videos=[Video(id="v1", title="One")],
        items=[PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0)],
    )
    db = make_db(tmp_path, snap)
    try:
        with pytest.raises(ValueError):
            build_plan(
                db,
                {"A": ["v1"], "B": ["v1"]},
                existing_targets={"A": "PL1", "B": "PL1"},
            )
    finally:
        db.close()


def test_build_plan_existing_target_all_present_fails(tmp_path):
    db = make_db(tmp_path, make_existing_snapshot())
    try:
        with pytest.raises(ValueError):
            build_plan(
                db,
                {"Existing": ["v1"]},
                existing_targets={"Existing": "PL1"},
            )
    finally:
        db.close()


def test_validate_add_video_both_and_neither_reference_rejected(tmp_path):
    db = make_db(tmp_path)
    try:
        both = [
            Operation(id="c1", kind="create_playlist", title="A"),
            Operation(
                id="a1",
                kind="add_video",
                playlist_ref="c1",
                playlist_id="PL1",
                video_id="v1",
                depends_on=["c1"],
            ),
        ]
        with pytest.raises(ValueError):
            validate_plan(db, make_plan(db, both))

        neither = [Operation(id="a1", kind="add_video", video_id="v1")]
        with pytest.raises(ValueError):
            validate_plan(db, make_plan(db, neither))
    finally:
        db.close()


def test_validate_add_video_playlist_id_no_create_needed(tmp_path):
    db = make_db(tmp_path, make_existing_snapshot())
    try:
        ops = [Operation(id="a1", kind="add_video", playlist_id="PL1", video_id="v2")]
        plan = make_plan(db, ops)
        validate_plan(db, plan)
    finally:
        db.close()


# -- add_video presence: present-but-blank fields rejected -------------------


def test_validate_rejects_blank_ref_alongside_valid_id():
    ops = [
        Operation(id="c1", kind="create_playlist", title="A"),
        Operation(
            id="a1",
            kind="add_video",
            playlist_ref="",
            playlist_id="PL1",
            video_id="v1",
            depends_on=["c1"],
        ),
    ]
    with pytest.raises(ValueError):
        _validate_operations(ops)


def test_validate_rejects_blank_id_alongside_valid_ref():
    ops = [
        Operation(id="c1", kind="create_playlist", title="A"),
        Operation(
            id="a1",
            kind="add_video",
            playlist_ref="c1",
            playlist_id="",
            video_id="v1",
            depends_on=["c1"],
        ),
    ]
    with pytest.raises(ValueError):
        _validate_operations(ops)


def test_validate_rejects_blank_ref_only():
    ops = [Operation(id="a1", kind="add_video", playlist_ref="", video_id="v1")]
    with pytest.raises(ValueError):
        _validate_operations(ops)


def test_validate_rejects_blank_id_only():
    ops = [Operation(id="a1", kind="add_video", playlist_id="", video_id="v1")]
    with pytest.raises(ValueError):
        _validate_operations(ops)


def test_validate_rejects_both_present_even_one_blank():
    ops = [
        Operation(id="c1", kind="create_playlist", title="A"),
        Operation(
            id="a1",
            kind="add_video",
            playlist_ref="c1",
            playlist_id="PL1",
            video_id="v1",
            depends_on=["c1"],
        ),
    ]
    with pytest.raises(ValueError):
        _validate_operations(ops)


def test_validate_accepts_valid_explicit_id_and_valid_ref():
    ops_ref = [
        Operation(id="c1", kind="create_playlist", title="A"),
        Operation(
            id="a1",
            kind="add_video",
            playlist_ref="c1",
            video_id="v1",
            depends_on=["c1"],
        ),
    ]
    _validate_operations(ops_ref)

    ops_id = [Operation(id="a1", kind="add_video", playlist_id="PL1", video_id="v1")]
    _validate_operations(ops_id)


# -- deduplicate_source with already-present existing target -----------------


def test_build_plan_dedup_existing_target_already_present_no_crash(tmp_path):
    playlists = [
        Playlist(id="PL_EXIST", title="Existing", channel_id="acct1", item_count=1),
        Playlist(id="PL_SRC", title="Source", channel_id="acct1", item_count=3),
    ]
    videos = [Video(id="v1", title="One"), Video(id="v2", title="Two")]
    items = [
        PlaylistItem(id="i0", playlist_id="PL_EXIST", video_id="v1", position=0),
        PlaylistItem(id="i1", playlist_id="PL_SRC", video_id="v1", position=0),
        PlaylistItem(id="i2", playlist_id="PL_SRC", video_id="v1", position=1),
        PlaylistItem(id="i3", playlist_id="PL_SRC", video_id="v2", position=2),
    ]
    db = make_db(
        tmp_path,
        make_snapshot(playlists=playlists, videos=videos, items=items),
    )
    try:
        plan = build_plan(
            db,
            {"Existing": ["v1", "v2"]},
            deduplicate_source=True,
            existing_targets={"Existing": "PL_EXIST"},
        )
        adds = [o for o in plan.operations if o.kind == "add_video"]
        assert [a.video_id for a in adds] == ["v2"]
        removes = [o for o in plan.operations if o.kind == "remove_item"]
        assert removes == []
    finally:
        db.close()
