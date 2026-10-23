"""SQLite inventory persistence for the YouTube manager."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from youtube_manager.models import Snapshot

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    account_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS playlists (
    id TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    removed_at TEXT
);

CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    removed_at TEXT
);

CREATE TABLE IF NOT EXISTS playlist_items (
    id TEXT PRIMARY KEY,
    playlist_id TEXT NOT NULL REFERENCES playlists(id),
    video_id TEXT NOT NULL REFERENCES videos(id),
    position INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    removed_at TEXT
);
"""

_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS content_evidence (
    id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(id),
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL,
    language TEXT,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recipe_analyses (
    id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(id),
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS migration_plans (
    id TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS migration_operations (
    plan_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    status TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (plan_id, operation_id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(model) -> str:
    return json.dumps(model.model_dump(mode="json"))


class Database:
    """SQLite-backed inventory store."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            self._conn.close()
            raise RuntimeError(
                f"database schema version {version} is newer than supported "
                f"{SCHEMA_VERSION}"
            )
        if version < SCHEMA_VERSION:
            try:
                self._conn.execute("BEGIN")
                for stmt in _SCHEMA.strip().split(";"):
                    if stmt.strip():
                        self._conn.execute(stmt)
                for stmt in _SCHEMA_V2.strip().split(";"):
                    if stmt.strip():
                        self._conn.execute(stmt)
                self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                self._conn.close()
                raise

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- internal helpers ---------------------------------------------------

    def _get_account_id(self) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM account_meta WHERE key = 'account_id'"
        ).fetchone()
        return row["value"] if row else None

    def _validate(self, snapshot: Snapshot) -> None:
        playlist_ids: set[str] = set()
        for p in snapshot.playlists:
            if p.id in playlist_ids:
                raise ValueError(f"duplicate playlist id: {p.id}")
            playlist_ids.add(p.id)

        video_ids: set[str] = set()
        for v in snapshot.videos:
            if v.id in video_ids:
                raise ValueError(f"duplicate video id: {v.id}")
            video_ids.add(v.id)

        item_ids: set[str] = set()
        for it in snapshot.items:
            if it.id in item_ids:
                raise ValueError(f"duplicate item id: {it.id}")
            item_ids.add(it.id)
            if it.playlist_id not in playlist_ids:
                raise ValueError(
                    f"item {it.id} references unknown playlist {it.playlist_id}"
                )
            if it.video_id not in video_ids:
                raise ValueError(
                    f"item {it.id} references unknown video {it.video_id}"
                )

    def apply_snapshot(self, snapshot: Snapshot) -> None:
        self._validate(snapshot)
        now = _now()
        conn = self._conn
        with conn:
            existing = self._get_account_id()
            if existing is None:
                conn.execute(
                    "INSERT INTO account_meta (key, value) VALUES ('account_id', ?)",
                    (snapshot.account_id,),
                )
            elif existing != snapshot.account_id:
                raise ValueError(
                    f"snapshot account {snapshot.account_id} does not match "
                    f"stored account {existing}"
                )

            playlist_ids = {p.id for p in snapshot.playlists}
            for p in snapshot.playlists:
                self._upsert_playlist(conn, p, now)

            for v in snapshot.videos:
                self._upsert_video(conn, v, now)

            item_ids = set()
            for it in snapshot.items:
                item_ids.add(it.id)
                self._upsert_item(conn, it, now)

            for row in conn.execute(
                "SELECT id FROM playlists WHERE removed_at IS NULL"
            ):
                if row["id"] not in playlist_ids:
                    conn.execute(
                        "UPDATE playlists SET removed_at = ? WHERE id = ?",
                        (now, row["id"]),
                    )

            for row in conn.execute(
                "SELECT id, playlist_id FROM playlist_items WHERE removed_at IS NULL"
            ):
                if row["playlist_id"] not in playlist_ids or row["id"] not in item_ids:
                    conn.execute(
                        "UPDATE playlist_items SET removed_at = ? WHERE id = ?",
                        (now, row["id"]),
                    )

            conn.execute(
                "INSERT INTO sync_runs (captured_at, account_id, snapshot_json) "
                "VALUES (?, ?, ?)",
                (snapshot.captured_at, snapshot.account_id, _dump(snapshot)),
            )

    def _upsert_playlist(self, conn, p, now: str) -> None:
        data = _dump(p)
        row = conn.execute(
            "SELECT 1 FROM playlists WHERE id = ?", (p.id,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE playlists SET data_json = ?, last_seen = ?, "
                "removed_at = NULL WHERE id = ?",
                (data, now, p.id),
            )
        else:
            conn.execute(
                "INSERT INTO playlists (id, data_json, first_seen, last_seen, "
                "removed_at) VALUES (?, ?, ?, ?, NULL)",
                (p.id, data, now, now),
            )

    @staticmethod
    def _merge_video(old: dict, new: dict) -> dict:
        availability = new.get("availability") or old.get("availability")
        no_details = availability in ("unavailable", "deleted") or (
            availability == "private" and not new.get("title")
        )
        if not no_details:
            return dict(new)
        merged = dict(new)
        for field in (
            "title",
            "description",
            "channel_id",
            "channel_title",
            "duration",
            "published_at",
            "privacy_status",
        ):
            if not new.get(field):
                merged[field] = old.get(field)
        merged["availability"] = availability
        return merged

    def _upsert_video(self, conn, v, now: str) -> None:
        new_data = v.model_dump(mode="json")
        row = conn.execute(
            "SELECT data_json FROM videos WHERE id = ?", (v.id,)
        ).fetchone()
        if row:
            merged = self._merge_video(json.loads(row["data_json"]), new_data)
            conn.execute(
                "UPDATE videos SET data_json = ?, last_seen = ?, "
                "removed_at = NULL WHERE id = ?",
                (json.dumps(merged), now, v.id),
            )
        else:
            conn.execute(
                "INSERT INTO videos (id, data_json, first_seen, last_seen, "
                "removed_at) VALUES (?, ?, ?, ?, NULL)",
                (v.id, json.dumps(new_data), now, now),
            )

    def _upsert_item(self, conn, it, now: str) -> None:
        data = _dump(it)
        row = conn.execute(
            "SELECT 1 FROM playlist_items WHERE id = ?", (it.id,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE playlist_items SET playlist_id = ?, video_id = ?, "
                "position = ?, data_json = ?, last_seen = ?, removed_at = NULL "
                "WHERE id = ?",
                (it.playlist_id, it.video_id, it.position, data, now, it.id),
            )
        else:
            conn.execute(
                "INSERT INTO playlist_items (id, playlist_id, video_id, position, "
                "data_json, first_seen, last_seen, removed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (it.id, it.playlist_id, it.video_id, it.position, data, now, now),
            )

    # -- queries ------------------------------------------------------------

    def list_playlists(self, include_removed: bool = False) -> list[dict]:
        query = "SELECT * FROM playlists"
        if not include_removed:
            query += " WHERE removed_at IS NULL"
        query += " ORDER BY id"
        return [self._playlist_row(r) for r in self._conn.execute(query)]

    @staticmethod
    def _playlist_row(r) -> dict:
        data = json.loads(r["data_json"])
        data["first_seen"] = r["first_seen"]
        data["last_seen"] = r["last_seen"]
        data["removed_at"] = r["removed_at"]
        return data

    def list_videos(
        self, playlist_id: str, include_removed: bool = False
    ) -> list[dict]:
        query = "SELECT * FROM playlist_items WHERE playlist_id = ?"
        params: list = [playlist_id]
        if not include_removed:
            query += " AND removed_at IS NULL"
        query += " ORDER BY position, id"
        results = []
        for r in self._conn.execute(query, params):
            video = self._conn.execute(
                "SELECT data_json FROM videos WHERE id = ?", (r["video_id"],)
            ).fetchone()
            item_data = json.loads(r["data_json"])
            results.append(
                {
                    "item_id": r["id"],
                    "playlist_id": r["playlist_id"],
                    "video_id": r["video_id"],
                    "position": r["position"],
                    "availability": item_data.get("availability"),
                    "first_seen": r["first_seen"],
                    "last_seen": r["last_seen"],
                    "removed_at": r["removed_at"],
                    "video": json.loads(video["data_json"]) if video else None,
                }
            )
        return results

    def summary(self) -> dict:
        def counts(table: str) -> dict:
            total = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            removed = self._conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE removed_at IS NOT NULL"
            ).fetchone()[0]
            return {"total": total, "removed": removed}

        return {
            "playlists": counts("playlists"),
            "videos": counts("videos"),
            "items": counts("playlist_items"),
        }

    def export(self) -> dict:
        playlists = [
            self._playlist_row(r)
            for r in self._conn.execute("SELECT * FROM playlists ORDER BY id")
        ]
        videos = []
        for r in self._conn.execute("SELECT * FROM videos ORDER BY id"):
            data = json.loads(r["data_json"])
            data["first_seen"] = r["first_seen"]
            data["last_seen"] = r["last_seen"]
            data["removed_at"] = r["removed_at"]
            videos.append(data)
        items = []
        for r in self._conn.execute(
            "SELECT * FROM playlist_items ORDER BY position, id"
        ):
            data = json.loads(r["data_json"])
            data["first_seen"] = r["first_seen"]
            data["last_seen"] = r["last_seen"]
            data["removed_at"] = r["removed_at"]
            items.append(data)
        runs = [
            json.loads(r["snapshot_json"])
            for r in self._conn.execute("SELECT snapshot_json FROM sync_runs")
        ]
        return {"playlists": playlists, "videos": videos, "items": items, "runs": runs}
