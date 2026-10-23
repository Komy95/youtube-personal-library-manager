"""Tests for Phase 2 content evidence capture."""

from __future__ import annotations

import pytest

import youtube_manager.content as content
from youtube_manager.content import Evidence, EvidenceStore
from youtube_manager.db import Database
from youtube_manager.models import Playlist, PlaylistItem, Snapshot, Video


def make_store(tmp_path, video_ids=("v1", "v2")):
    db = Database(tmp_path / "inventory.sqlite3")
    playlists = [Playlist(id="PL1", title="List", item_count=len(video_ids))]
    videos = [Video(id=v, title=f"Video {v}") for v in video_ids]
    items = [
        PlaylistItem(id=f"i{n}", playlist_id="PL1", video_id=v, position=n)
        for n, v in enumerate(video_ids)
    ]
    db.apply_snapshot(
        Snapshot(account_id="acct1", playlists=playlists, videos=videos, items=items)
    )
    return db, EvidenceStore(db)


def test_evidence_model_fields_and_hash():
    ev = Evidence(
        video_id="v1",
        kind="transcript",
        content="Hello <world> &amp; all",
        source="notes.txt",
        language="en",
    )
    assert ev.content_hash == Evidence(
        video_id="v1", kind="transcript", content="Hello <world> &amp; all",
        source="notes.txt", language="en",
    ).content_hash
    assert ev.id == Evidence.compute_id(
        "v1", "transcript", "Hello <world> &amp; all", "notes.txt", "en"
    )
    assert ev.created_at


def test_evidence_rejects_extra_fields():
    with pytest.raises(ValueError):
        Evidence(
            video_id="v1", kind="transcript", content="x", source="s", bogus=1
        )


def test_evidence_validation_constraints():
    with pytest.raises(ValueError):
        Evidence(video_id="", kind="transcript", content="x", source="s")
    with pytest.raises(ValueError):
        Evidence(video_id="v1", kind="audio", content="x", source="s")
    with pytest.raises(ValueError):
        Evidence(video_id="v1", kind="transcript", content="", source="s")
    with pytest.raises(ValueError):
        Evidence(video_id="v1", kind="transcript", content="x", source="")
    with pytest.raises(ValueError):
        Evidence(
            video_id="v1", kind="transcript", content="x", source="s",
            language="x" * 33,
        )


def test_import_txt_normalizes_entities(tmp_path):
    db, store = make_store(tmp_path)
    try:
        path = tmp_path / "t.txt"
        path.write_text("Hello <b>world</b> &amp; goodbye &lt;3", encoding="utf-8")
        ev = store.import_text("v1", path, source="t.txt")
        assert ev.content == "Hello <b>world</b> & goodbye <3"
        assert ev.source == "t.txt"
    finally:
        db.close()


def test_import_txt_preserves_angle_brackets(tmp_path):
    db, store = make_store(tmp_path)
    try:
        path = tmp_path / "t.txt"
        path.write_text("x < y > z <code>keep</code>", encoding="utf-8")
        ev = store.import_text("v1", path)
        assert ev.content == "x < y > z <code>keep</code>"
    finally:
        db.close()


def test_import_srt_strips_timing_and_index(tmp_path):
    db, store = make_store(tmp_path)
    try:
        path = tmp_path / "t.srt"
        path.write_text(
            "1\n00:00:00,000 --> 00:00:02,000\nHello there\n\n"
            "2\n00:00:02,000 --> 00:00:04,000\nGeneral Kenobi\n",
            encoding="utf-8",
        )
        ev = store.import_text("v1", path)
        assert "Hello there" in ev.content
        assert "General Kenobi" in ev.content
        assert "-->" not in ev.content
        assert "1\n" not in ev.content
    finally:
        db.close()


def test_import_vtt_strips_webvtt_header_and_timing(tmp_path):
    db, store = make_store(tmp_path)
    try:
        path = tmp_path / "t.vtt"
        path.write_text(
            "WEBVTT\n\nNOTE this is ignored\n\n"
            "00:00:00.000 --> 00:00:02.000\nFirst line\n"
            "00:00:02.000 --> 00:00:04.000\nSecond line\n",
            encoding="utf-8",
        )
        ev = store.import_text("v1", path)
        assert "WEBVTT" not in ev.content
        assert "NOTE" not in ev.content
        assert "First line" in ev.content
        assert "Second line" in ev.content
    finally:
        db.close()


def test_import_unknown_video_rejected(tmp_path):
    db, store = make_store(tmp_path)
    try:
        path = tmp_path / "t.txt"
        path.write_text("hello", encoding="utf-8")
        with pytest.raises(ValueError):
            store.import_text("nope", path)
    finally:
        db.close()


def test_import_oversized_file_rejected(tmp_path):
    db, store = make_store(tmp_path)
    try:
        path = tmp_path / "big.txt"
        path.write_text("x" * (2 * 1024 * 1024 + 1), encoding="utf-8")
        with pytest.raises(ValueError):
            store.import_text("v1", path)
    finally:
        db.close()


def test_import_empty_and_timing_only_rejected(tmp_path):
    db, store = make_store(tmp_path)
    try:
        empty = tmp_path / "empty.txt"
        empty.write_text("", encoding="utf-8")
        with pytest.raises(ValueError):
            store.import_text("v1", empty)

        timing = tmp_path / "timing.srt"
        timing.write_text(
            "1\n00:00:00,000 --> 00:00:02,000\n\n2\n00:00:02,000 --> 00:00:04,000\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            store.import_text("v1", timing)
    finally:
        db.close()


def test_import_unsupported_extension_rejected(tmp_path):
    db, store = make_store(tmp_path)
    try:
        path = tmp_path / "t.md"
        path.write_text("hi", encoding="utf-8")
        with pytest.raises(ValueError):
            store.import_text("v1", path)
    finally:
        db.close()


def test_duplicate_import_idempotent_retains_created_at(tmp_path):
    db, store = make_store(tmp_path)
    try:
        path = tmp_path / "t.txt"
        path.write_text("same text", encoding="utf-8")
        first = store.import_text("v1", path)
        second = store.import_text("v1", path)
        assert first.id == second.id
        assert first.created_at == second.created_at
        assert len(store.list_evidence()) == 1
    finally:
        db.close()


def test_changed_text_creates_new_evidence(tmp_path):
    db, store = make_store(tmp_path)
    try:
        path = tmp_path / "t.txt"
        path.write_text("first version", encoding="utf-8")
        first = store.import_text("v1", path)
        path.write_text("second version", encoding="utf-8")
        second = store.import_text("v1", path)
        assert first.id != second.id
        assert len(store.list_evidence("v1")) == 2
    finally:
        db.close()


def test_add_evidence_helper(tmp_path):
    db, store = make_store(tmp_path)
    try:
        ev = store.add_evidence("v1", "manual note", "notes", "me")
        assert ev.kind == "notes"
        assert store.list_evidence("v1")[0].content == "manual note"
    finally:
        db.close()


def test_transcript_ordering(tmp_path):
    db, store = make_store(tmp_path)
    try:
        store.add_evidence("v1", "first", "transcript", "a")
        store.add_evidence("v1", "second", "transcript", "b")
        store.add_evidence("v1", "third", "transcript", "c")
        contents = [e.content for e in store.list_evidence("v1")]
        assert contents == ["first", "second", "third"]
    finally:
        db.close()


def test_same_timestamp_imports_preserve_input_order(tmp_path, monkeypatch):
    db, store = make_store(tmp_path)
    monkeypatch.setattr(content, "_now_utc", lambda: "2026-01-01T00:00:00+00:00")
    try:
        store.add_evidence("v1", "first", "transcript", "a")
        store.add_evidence("v1", "second", "transcript", "b")
        store.add_evidence("v1", "third", "transcript", "c")
        contents = [e.content for e in store.list_evidence("v1")]
        assert contents == ["first", "second", "third"]
        store.add_evidence("v1", "second", "transcript", "b")
        contents = [e.content for e in store.list_evidence("v1")]
        assert contents == ["first", "second", "third"]
    finally:
        db.close()


def test_coverage(tmp_path):
    db, store = make_store(tmp_path, video_ids=("v1", "v2", "v3"))
    try:
        assert store.coverage() == {
            "totalVideos": 3,
            "withEvidence": 0,
            "withoutEvidence": 3,
        }
        store.add_evidence("v1", "x", "transcript", "a")
        assert store.coverage() == {
            "totalVideos": 3,
            "withEvidence": 1,
            "withoutEvidence": 2,
        }
    finally:
        db.close()


def test_evidence_immutable_no_overwrite(tmp_path):
    db, store = make_store(tmp_path)
    try:
        first = store.add_evidence("v1", "old content", "transcript", "a")
        store.add_evidence("v1", "old content", "transcript", "a")
        rows = db._conn.execute(
            "SELECT content FROM content_evidence WHERE video_id = 'v1'"
        ).fetchall()
        assert [r["content"] for r in rows] == ["old content"]
        assert first.id in [e.id for e in store.list_evidence("v1")]
    finally:
        db.close()


def test_pipe_content_and_source_produce_distinct_records(tmp_path):
    db, store = make_store(tmp_path)
    try:
        e1 = store.add_evidence("v1", "a|b", "transcript", "c")
        e2 = store.add_evidence("v1", "a", "transcript", "b|c")
        assert e1.id != e2.id
        assert len(store.list_evidence("v1")) == 2
    finally:
        db.close()


def test_evidence_rejects_whitespace_only_fields():
    with pytest.raises(ValueError):
        Evidence(video_id="   ", kind="transcript", content="x", source="s")
    with pytest.raises(ValueError):
        Evidence(video_id="v1", kind="transcript", content="   ", source="s")
    with pytest.raises(ValueError):
        Evidence(video_id="v1", kind="transcript", content="x", source="   ")


def test_row_to_evidence_rejects_corrupted_content_hash(tmp_path):
    db, store = make_store(tmp_path)
    try:
        ev = store.add_evidence("v1", "hello", "transcript", "s")
        with db._conn:
            db._conn.execute(
                "UPDATE content_evidence SET content_hash = 'bad' WHERE id = ?",
                (ev.id,),
            )
        with pytest.raises(ValueError):
            store.list_evidence("v1")
    finally:
        db.close()


def test_row_to_evidence_rejects_corrupted_id(tmp_path):
    db, store = make_store(tmp_path)
    try:
        ev = store.add_evidence("v1", "hello", "transcript", "s")
        with db._conn:
            db._conn.execute(
                "UPDATE content_evidence SET id = 'bad' WHERE id = ?",
                (ev.id,),
            )
        with pytest.raises(ValueError):
            store.list_evidence("v1")
    finally:
        db.close()


def test_import_utf8_bom_stripped(tmp_path):
    db, store = make_store(tmp_path)
    try:
        path = tmp_path / "t.vtt"
        path.write_text(
            "\ufeffWEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello\n",
            encoding="utf-8",
        )
        ev = store.import_text("v1", path)
        assert "WEBVTT" not in ev.content
        assert "Hello" in ev.content
    finally:
        db.close()


def test_import_vtt_supports_minute_and_hour_timestamps(tmp_path):
    db, store = make_store(tmp_path)
    try:
        path = tmp_path / "t.vtt"
        path.write_text(
            "WEBVTT\n\n"
            "00:01.500 --> 00:02.000\nShort stamp\n\n"
            "01:02:03.000 --> 01:02:04.000\nLong stamp\n",
            encoding="utf-8",
        )
        ev = store.import_text("v1", path)
        assert "Short stamp" in ev.content
        assert "Long stamp" in ev.content
        assert "-->" not in ev.content
    finally:
        db.close()


def test_import_preserves_numeric_and_note_payload(tmp_path):
    db, store = make_store(tmp_path)
    try:
        path = tmp_path / "t.vtt"
        path.write_text(
            "WEBVTT\n\n"
            "NOTE outside comment\n\n"
            "00:00:00.000 --> 00:00:02.000\n123\nNote this stays\n2020\n",
            encoding="utf-8",
        )
        ev = store.import_text("v1", path)
        assert "123" in ev.content
        assert "Note this stays" in ev.content
        assert "2020" in ev.content
        assert "outside comment" not in ev.content
    finally:
        db.close()


def test_import_vtt_ignores_named_cue_identifier_and_header_metadata(tmp_path):
    db, store = make_store(tmp_path)
    try:
        path = tmp_path / "t.vtt"
        path.write_text(
            "WEBVTT\n\n"
            "Kind: captions\nLanguage: en\n\n"
            "intro\n00:00:00.000 --> 00:00:02.000\nHello world\n\n"
            "outro\n00:00:02.000 --> 00:00:04.000\nGoodbye\n",
            encoding="utf-8",
        )
        ev = store.import_text("v1", path)
        assert "Hello world" in ev.content
        assert "Goodbye" in ev.content
        assert "intro" not in ev.content
        assert "outro" not in ev.content
        assert "Kind: captions" not in ev.content
        assert "Language: en" not in ev.content
    finally:
        db.close()


def test_import_vtt_named_identifier_only_rejected(tmp_path):
    db, store = make_store(tmp_path)
    try:
        path = tmp_path / "t.vtt"
        path.write_text(
            "WEBVTT\n\nintro\n00:00:00.000 --> 00:00:02.000\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            store.import_text("v1", path)
    finally:
        db.close()


def test_import_skips_style_and_region_blocks(tmp_path):
    db, store = make_store(tmp_path)
    try:
        path = tmp_path / "t.vtt"
        path.write_text(
            "WEBVTT\n\n"
            "STYLE\n::cue { color: white }\n\n"
            "REGION\nid:fred\n\n"
            "00:00:00.000 --> 00:00:01.000\nVisible\n",
            encoding="utf-8",
        )
        ev = store.import_text("v1", path)
        assert "Visible" in ev.content
        assert "::cue" not in ev.content
        assert "fred" not in ev.content
    finally:
        db.close()
