"""Tests for the sync orchestration layer."""

from __future__ import annotations

import pytest

from youtube_manager.db import Database
from youtube_manager.models import Availability, Playlist, PlaylistItem, Video
from youtube_manager.sync import synchronize


class FakeClient:
    def __init__(self, account=None, playlists=None, items=None, videos=None):
        self._account = account or {"id": "acct1", "title": "Me"}
        self._playlists = playlists if playlists is not None else []
        self._items = items if items is not None else {}
        self._videos = videos if videos is not None else {}

    def account(self):
        return self._account

    def list_playlists(self):
        return self._playlists

    def list_playlist_items(self, playlist_id):
        return self._items.get(playlist_id, [])

    def list_videos(self, video_ids):
        return [
            self._videos.get(vid, Video(id=vid, availability=Availability.unavailable))
            for vid in video_ids
        ]


def make_video(vid, title="", availability=Availability.available, description=""):
    return Video(id=vid, title=title, availability=availability, description=description)


def test_failure_on_second_playlist_leaves_db_unchanged(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    playlists = [
        Playlist(id="PL1", title="One"),
        Playlist(id="PL2", title="Two"),
    ]
    items = {
        "PL1": [PlaylistItem(id="i1", playlist_id="PL1", video_id="v1")],
    }
    videos = {"v1": make_video("v1", "Video One")}
    client = FakeClient(playlists=playlists, items=items, videos=videos)

    try:
        result = synchronize(client, db)
        before = db.export()

        class FailingClient(FakeClient):
            def list_playlist_items(self, playlist_id):
                if playlist_id == "PL2":
                    raise RuntimeError("boom")
                return super().list_playlist_items(playlist_id)

        failing = FailingClient(playlists=playlists, items=items, videos=videos)
        try:
            synchronize(failing, db)
        except RuntimeError:
            pass
        assert db.export() == before
        assert result["playlists"]["total"] == 2
    finally:
        db.close()


def test_video_batch_failure_leaves_db_unchanged(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    playlists = [Playlist(id="PL1", title="One")]
    items = {"PL1": [PlaylistItem(id="i1", playlist_id="PL1", video_id="v1")]}

    class FailingClient(FakeClient):
        def list_videos(self, video_ids):
            raise RuntimeError("boom")

    client = FailingClient(playlists=playlists, items=items)
    try:
        with pytest.raises(RuntimeError):
            synchronize(client, db)
        assert db.export()["runs"] == []
        assert db.export()["playlists"] == []
    finally:
        db.close()


def test_repeated_sync_preserves_ordering_and_duplicates(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    playlists = [Playlist(id="PL1", title="One")]
    items = {
        "PL1": [
            PlaylistItem(id="i1", playlist_id="PL1", video_id="v1", position=0),
            PlaylistItem(id="i2", playlist_id="PL1", video_id="v1", position=1),
            PlaylistItem(id="i3", playlist_id="PL1", video_id="v2", position=0),
        ]
    }
    videos = {
        "v1": make_video("v1", "Video One"),
        "v2": make_video("v2", "Video Two"),
    }
    client = FakeClient(playlists=playlists, items=items, videos=videos)
    try:
        synchronize(client, db)
        synchronize(client, db)
        result = db.list_videos("PL1")
        assert [r["item_id"] for r in result] == ["i1", "i3", "i2"]
        assert len(db.export()["runs"]) == 2
    finally:
        db.close()


def test_removed_playlist_history_queryable(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    playlists = [Playlist(id="PL1", title="One")]
    items = {"PL1": [PlaylistItem(id="i1", playlist_id="PL1", video_id="v1")]}
    videos = {"v1": make_video("v1", "Video One")}
    try:
        synchronize(FakeClient(playlists=playlists, items=items, videos=videos), db)
        synchronize(FakeClient(), db)
        removed = db.list_videos("PL1", include_removed=True)
        assert len(removed) == 1
        assert removed[0]["item_id"] == "i1"
        assert removed[0]["removed_at"] is not None
    finally:
        db.close()


def test_unavailable_vs_private_distinction(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    playlists = [Playlist(id="PL1", title="One")]
    items = {
        "PL1": [
            PlaylistItem(
                id="i1",
                playlist_id="PL1",
                video_id="v1",
                title="Private video",
                availability=Availability.private,
            ),
            PlaylistItem(id="i2", playlist_id="PL1", video_id="v2", title="Gone"),
        ]
    }
    videos = {
        "v1": Video(id="v1", availability=Availability.unavailable),
        "v2": Video(id="v2", availability=Availability.unavailable),
    }
    client = FakeClient(playlists=playlists, items=items, videos=videos)
    try:
        synchronize(client, db)
        export = db.export()
        video_map = {v["id"]: v for v in export["videos"]}
        assert video_map["v1"]["availability"] == "private"
        assert video_map["v2"]["availability"] == "unavailable"
    finally:
        db.close()


def test_private_video_preserves_prior_metadata(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    playlists = [Playlist(id="PL1", title="One")]
    try:
        items = {"PL1": [PlaylistItem(id="i1", playlist_id="PL1", video_id="v1")]}
        videos = {
            "v1": Video(
                id="v1",
                title="Video One",
                duration="PT1M",
                channel_title="Chan",
            )
        }
        synchronize(FakeClient(playlists=playlists, items=items, videos=videos), db)

        items = {
            "PL1": [
                PlaylistItem(
                    id="i1",
                    playlist_id="PL1",
                    video_id="v1",
                    title="Private video",
                    availability=Availability.private,
                )
            ]
        }
        videos = {"v1": Video(id="v1", availability=Availability.unavailable)}
        synchronize(FakeClient(playlists=playlists, items=items, videos=videos), db)

        video_map = {v["id"]: v for v in db.export()["videos"]}
        v1 = video_map["v1"]
        assert v1["availability"] == "private"
        assert v1["title"] == "Video One"
        assert v1["duration"] == "PT1M"
        assert v1["channel_title"] == "Chan"
    finally:
        db.close()


def test_available_description_cleared(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    playlists = [Playlist(id="PL1", title="One")]
    items = {"PL1": [PlaylistItem(id="i1", playlist_id="PL1", video_id="v1")]}

    try:
        videos = {"v1": make_video("v1", "Video One", description="Old")}
        synchronize(FakeClient(playlists=playlists, items=items, videos=videos), db)
        videos = {"v1": make_video("v1", "Video One", description="")}
        synchronize(FakeClient(playlists=playlists, items=items, videos=videos), db)
        export = db.export()
        video_map = {v["id"]: v for v in export["videos"]}
        assert video_map["v1"]["description"] == ""
        assert video_map["v1"]["availability"] == "available"
    finally:
        db.close()
