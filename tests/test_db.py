"""Tests for the SQLite inventory database."""

from __future__ import annotations

import json
import sqlite3

import pytest

from youtube_manager.db import Database
from youtube_manager.models import (
    Availability,
    Playlist,
    PlaylistItem,
    Snapshot,
    Video,
)


def make_snapshot(account_id="acct1"):
    playlists = [Playlist(id="PL1", title="My List", item_count=2)]
    videos = [
        Video(id="v1", title="Video One", duration="PT1M", channel_title="Chan"),
        Video(id="v2", title="Video Two", duration="PT2M"),
    ]
    items = [
        PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0),
        PlaylistItem(id="i2", playlist_id="PL1", video_id="v2", position=1),
    ]
    return Snapshot(account_id=account_id, playlists=playlists, videos=videos, items=items)


def test_init_creates_parent_and_schema(tmp_path):
    path = tmp_path / "nested" / "inventory.sqlite3"
    db = Database(path)
    try:
        assert path.exists()
        conn = db._conn
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 2
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
    finally:
        db.close()


def test_reject_future_version(tmp_path):
    path = tmp_path / "inventory.sqlite3"
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError):
        Database(path)


def test_context_manager(tmp_path):
    path = tmp_path / "inventory.sqlite3"
    with Database(path) as db:
        db.apply_snapshot(make_snapshot())
        assert len(db.list_playlists()) == 1
    assert path.exists()


def test_apply_snapshot_idempotent_history_accumulates(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    try:
        snap = make_snapshot()
        db.apply_snapshot(snap)
        db.apply_snapshot(snap)
        assert len(db.list_playlists()) == 1
        assert len(db.list_videos("PL1")) == 2
        runs = db.export()["runs"]
        assert len(runs) == 2
    finally:
        db.close()


def test_duplicate_video_preserved_by_item_ids(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    try:
        playlists = [Playlist(id="PL1", title="Dup")]
        videos = [Video(id="v1", title="Same Video")]
        items = [
            PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0),
            PlaylistItem(id="i2", playlist_id="PL1", video_id="v1", position=1),
        ]
        db.apply_snapshot(
            Snapshot(
                account_id="acct1",
                playlists=playlists,
                videos=videos,
                items=items,
            )
        )
        result = db.list_videos("PL1")
        assert len(result) == 2
        assert {r["item_id"] for r in result} == {"i1", "i2"}
        assert {r["video_id"] for r in result} == {"v1"}
    finally:
        db.close()


def test_reordered_positions(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    try:
        db.apply_snapshot(make_snapshot())
        playlists = [Playlist(id="PL1", title="My List", item_count=2)]
        videos = [
            Video(id="v1", title="Video One"),
            Video(id="v2", title="Video Two"),
        ]
        items = [
            PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=1),
            PlaylistItem(id="i2", playlist_id="PL1", video_id="v2", position=0),
        ]
        db.apply_snapshot(
            Snapshot(account_id="acct1", playlists=playlists, videos=videos, items=items)
        )
        result = db.list_videos("PL1")
        assert [r["item_id"] for r in result] == ["i2", "i1"]
    finally:
        db.close()


def test_soft_removal_and_revival(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    try:
        db.apply_snapshot(make_snapshot())
        playlists = [Playlist(id="PL1", title="My List", item_count=1)]
        videos = [Video(id="v1", title="Video One")]
        items = [PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0)]
        db.apply_snapshot(
            Snapshot(account_id="acct1", playlists=playlists, videos=videos, items=items)
        )
        assert len(db.list_videos("PL1")) == 1
        removed = db.list_videos("PL1", include_removed=True)
        assert any(r["item_id"] == "i2" and r["removed_at"] for r in removed)

        db.apply_snapshot(make_snapshot())
        result = db.list_videos("PL1")
        assert len(result) == 2
        assert all(r["removed_at"] is None for r in result)
    finally:
        db.close()


def test_account_mismatch_rejects_without_updates(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    try:
        db.apply_snapshot(make_snapshot(account_id="acct1"))
        with pytest.raises(ValueError):
            db.apply_snapshot(make_snapshot(account_id="acct2"))
        assert db._get_account_id() == "acct1"
        assert len(db.export()["runs"]) == 1
    finally:
        db.close()


def test_invalid_item_reference_atomic_failure(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    try:
        snap = make_snapshot()
        snap.items.append(
            PlaylistItem(id="bad", playlist_id="PL1", video_id="missing", position=9)
        )
        with pytest.raises(ValueError):
            db.apply_snapshot(snap)
        assert db.list_playlists() == []
        assert db.export()["runs"] == []
    finally:
        db.close()


def test_duplicate_ids_rejected(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    try:
        playlists = [Playlist(id="PL1"), Playlist(id="PL1")]
        with pytest.raises(ValueError):
            db.apply_snapshot(
                Snapshot(account_id="acct1", playlists=playlists)
            )
    finally:
        db.close()


def test_missing_video_details_preserve_old(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    try:
        db.apply_snapshot(make_snapshot())
        playlists = [Playlist(id="PL1", title="My List", item_count=2)]
        videos = [
            Video(id="v1", title="", duration=None, availability=Availability.unavailable),
            Video(id="v2", title="Video Two", duration="PT2M"),
        ]
        items = [
            PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0),
            PlaylistItem(id="i2", playlist_id="PL1", video_id="v2", position=1),
        ]
        db.apply_snapshot(
            Snapshot(account_id="acct1", playlists=playlists, videos=videos, items=items)
        )
        result = {r["video_id"]: r["video"] for r in db.list_videos("PL1")}
        v1 = result["v1"]
        assert v1["title"] == "Video One"
        assert v1["duration"] == "PT1M"
        assert v1["availability"] == "unavailable"
    finally:
        db.close()


def test_available_video_cleared_description_reflected(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    try:
        playlists = [Playlist(id="PL1", title="My List", item_count=1)]
        videos = [Video(id="v1", title="Video One", description="Old description")]
        items = [PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0)]
        db.apply_snapshot(
            Snapshot(account_id="acct1", playlists=playlists, videos=videos, items=items)
        )
        videos = [Video(id="v1", title="Video One", description="")]
        db.apply_snapshot(
            Snapshot(account_id="acct1", playlists=playlists, videos=videos, items=items)
        )
        result = {r["video_id"]: r["video"] for r in db.list_videos("PL1")}
        assert result["v1"]["description"] == ""
        assert result["v1"]["availability"] == "available"
    finally:
        db.close()


def test_private_video_with_title_reflects_cleared_description(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    try:
        playlists = [Playlist(id="PL1", title="My List", item_count=1)]
        videos = [
            Video(id="v1", title="Video One", description="Old description", duration="PT1M")
        ]
        items = [PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0)]
        db.apply_snapshot(
            Snapshot(account_id="acct1", playlists=playlists, videos=videos, items=items)
        )
        videos = [
            Video(
                id="v1",
                title="Video One",
                description="",
                availability=Availability.private,
            )
        ]
        db.apply_snapshot(
            Snapshot(account_id="acct1", playlists=playlists, videos=videos, items=items)
        )
        result = {r["video_id"]: r["video"] for r in db.list_videos("PL1")}
        v1 = result["v1"]
        assert v1["description"] == ""
        assert v1["availability"] == "private"
        assert v1["title"] == "Video One"
    finally:
        db.close()


def test_summary_counts(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    try:
        db.apply_snapshot(make_snapshot())
        s = db.summary()
        assert s["playlists"]["total"] == 1
        assert s["videos"]["total"] == 2
        assert s["items"]["total"] == 2
        assert s["playlists"]["removed"] == 0
    finally:
        db.close()


def test_removed_playlist_soft_deletes_items(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    try:
        db.apply_snapshot(make_snapshot())
        db.apply_snapshot(
            Snapshot(account_id="acct1", playlists=[], videos=[], items=[])
        )
        assert db.list_playlists() == []
        assert db.list_playlists(include_removed=True)[0]["removed_at"] is not None
        items = db.list_videos("PL1", include_removed=True)
        assert len(items) == 2
        assert all(r["removed_at"] is not None for r in items)
    finally:
        db.close()


def test_upsert_failure_rolls_back_transaction(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    try:
        db.apply_snapshot(make_snapshot())
        before = db.export()

        db._conn.execute(
            "CREATE TRIGGER fail_insert AFTER INSERT ON videos "
            "WHEN NEW.id = 'boom' BEGIN SELECT RAISE(ABORT, 'boom'); END"
        )
        db._conn.commit()

        playlists = [Playlist(id="PL1", title="My List", item_count=3)]
        videos = [
            Video(id="v1", title="Video One", duration="PT1M", channel_title="Chan"),
            Video(id="v2", title="Video Two", duration="PT2M"),
            Video(id="boom", title="Boom"),
        ]
        items = [
            PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0),
            PlaylistItem(id="i2", playlist_id="PL1", video_id="v2", position=1),
            PlaylistItem(id="i3", playlist_id="PL1", video_id="boom", position=2),
        ]
        with pytest.raises(sqlite3.IntegrityError):
            db.apply_snapshot(
                Snapshot(account_id="acct1", playlists=playlists, videos=videos, items=items)
            )
        assert db.export() == before
    finally:
        db.close()


def test_stored_snapshot_history_immutable(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    try:
        db.apply_snapshot(make_snapshot())
        first_run = db.export()["runs"][0]

        playlists = [Playlist(id="PL1", title="My List", item_count=2)]
        videos = [
            Video(id="v1", title="Renamed", duration="PT1M", channel_title="Chan"),
            Video(id="v2", title="Video Two", duration="PT2M"),
        ]
        items = [
            PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0),
            PlaylistItem(id="i2", playlist_id="PL1", video_id="v2", position=1),
        ]
        db.apply_snapshot(
            Snapshot(account_id="acct1", playlists=playlists, videos=videos, items=items)
        )
        assert db.export()["runs"][0] == first_run
        assert db.export()["runs"][0]["videos"][0]["title"] == "Video One"
    finally:
        db.close()


def test_v1_to_v2_migration_preserves_rows(tmp_path):
    path = tmp_path / "inventory.sqlite3"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE account_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT NOT NULL,
            account_id TEXT NOT NULL,
            snapshot_json TEXT NOT NULL
        );
        CREATE TABLE playlists (
            id TEXT PRIMARY KEY, data_json TEXT NOT NULL, first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL, removed_at TEXT
        );
        CREATE TABLE videos (
            id TEXT PRIMARY KEY, data_json TEXT NOT NULL, first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL, removed_at TEXT
        );
        CREATE TABLE playlist_items (
            id TEXT PRIMARY KEY, playlist_id TEXT NOT NULL REFERENCES playlists(id),
            video_id TEXT NOT NULL REFERENCES videos(id), position INTEGER NOT NULL,
            data_json TEXT NOT NULL, first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL, removed_at TEXT
        );
        INSERT INTO account_meta (key, value) VALUES ('account_id', 'acct1');
        INSERT INTO videos (id, data_json, first_seen, last_seen, removed_at)
            VALUES ('v1', '{"id":"v1","title":"Old"}', 't', 't', NULL);
        INSERT INTO sync_runs (captured_at, account_id, snapshot_json)
            VALUES ('t', 'acct1', '{"account_id":"acct1"}');
        PRAGMA user_version = 1;
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    try:
        assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.export()["videos"][0]["id"] == "v1"
        assert db.export()["runs"] == [{"account_id": "acct1"}]
        assert db._get_account_id() == "acct1"
        for table in (
            "content_evidence",
            "recipe_analyses",
            "migration_plans",
            "migration_operations",
        ):
            db._conn.execute(f"SELECT * FROM {table}").fetchall()
    finally:
        db.close()
