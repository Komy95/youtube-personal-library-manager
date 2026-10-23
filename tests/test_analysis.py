"""Tests for Phase 2 recipe analysis."""

from __future__ import annotations

import json

import pytest

from youtube_manager import analysis
from youtube_manager.analysis import AnalysisError, analyze_video, category_candidates, list_analyses, save_analysis
from youtube_manager.content import EvidenceStore
from youtube_manager.db import Database
from youtube_manager.models import Playlist, PlaylistItem, Snapshot, Video


class FakeProvider:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def generate(self, messages, schema):
        self.calls.append((messages, schema))
        return {"data": self.data}


def make_env(tmp_path, video_ids=("v1",), contents=("Mix 2 cups flour with 1 egg.",)):
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
    store = EvidenceStore(db)
    evidence = [
        store.add_evidence(video_ids[0], c, "transcript", "notes.txt")
        for c in contents
    ]
    return db, store, evidence


def recipe_payload(ev_id, quote="flour"):
    return {
        "is_recipe": True,
        "title": {"value": "Flour Cake", "evidence_id": ev_id, "quote": quote},
        "ingredients": [
            {"value": "flour", "evidence_id": ev_id, "quote": quote},
            {"value": "egg", "evidence_id": ev_id, "quote": "egg"},
        ],
        "steps": [{"value": "mix", "evidence_id": ev_id, "quote": "Mix"}],
        "tags": [{"value": "baking", "evidence_id": ev_id, "quote": "flour"}],
    }


def test_grounded_analysis_accepted_and_stable_id(tmp_path):
    db, store, evidence = make_env(tmp_path)
    ev = evidence[0]
    payload = recipe_payload(ev.id)
    a = analyze_video(db, "v1", FakeProvider(payload), "test-model")
    assert a.is_recipe is True
    assert a.title.value == "Flour Cake"
    assert a.evidence_ids == [ev.id]
    assert a.id == a.id
    a2 = save_analysis(db, "v1", payload, "test-model")
    assert a2.id == a.id
    rows = db._conn.execute(
        "SELECT id FROM recipe_analyses WHERE video_id = 'v1'"
    ).fetchall()
    assert len(rows) == 1


def test_hallucinated_quote_rejected(tmp_path):
    db, store, evidence = make_env(tmp_path)
    ev = evidence[0]
    payload = recipe_payload(ev.id, quote="totally absent text")
    with pytest.raises(AnalysisError):
        analyze_video(db, "v1", FakeProvider(payload), "m")


def test_wrong_video_citation_rejected(tmp_path):
    db, store, evidence = make_env(tmp_path, video_ids=("v1", "v2"))
    v2_ev = store.add_evidence("v2", "v2 words.", "transcript", "notes.txt")
    payload = recipe_payload(v2_ev.id, quote="v2")
    with pytest.raises(AnalysisError):
        analyze_video(db, "v1", FakeProvider(payload), "m")


def test_unknown_evidence_rejected(tmp_path):
    db, store, evidence = make_env(tmp_path)
    payload = recipe_payload("does-not-exist")
    with pytest.raises(AnalysisError):
        analyze_video(db, "v1", FakeProvider(payload), "m")


def test_no_evidence_no_provider_call(tmp_path):
    db = Database(tmp_path / "inventory.sqlite3")
    playlists = [Playlist(id="PL1", title="List", item_count=1)]
    videos = [Video(id="v1", title="Video v1")]
    items = [PlaylistItem(id="i0", playlist_id="PL1", video_id="v1", position=0)]
    db.apply_snapshot(
        Snapshot(account_id="acct", playlists=playlists, videos=videos, items=items)
    )
    provider = FakeProvider(None)
    with pytest.raises(AnalysisError):
        analyze_video(db, "v1", provider, "m")
    assert provider.calls == []


def test_recipe_requires_title_and_fact(tmp_path):
    db, store, evidence = make_env(tmp_path)
    ev = evidence[0]
    no_title = recipe_payload(ev.id)
    no_title["title"] = None
    with pytest.raises(AnalysisError):
        analyze_video(db, "v1", FakeProvider(no_title), "m")

    no_facts = recipe_payload(ev.id)
    no_facts["ingredients"] = []
    no_facts["steps"] = []
    with pytest.raises(AnalysisError):
        analyze_video(db, "v1", FakeProvider(no_facts), "m")


def test_non_recipe_may_have_no_facts(tmp_path):
    db, store, evidence = make_env(tmp_path)
    ev = evidence[0]
    a = save_analysis(db, "v1", {"is_recipe": False}, "m")
    assert a.is_recipe is False
    assert a.title is None
    assert a.ingredients == []


def test_duplicate_and_oversized_lists_rejected(tmp_path):
    db, store, evidence = make_env(tmp_path)
    ev = evidence[0]
    dup = recipe_payload(ev.id)
    dup["ingredients"] = dup["ingredients"] * 2
    with pytest.raises(AnalysisError):
        analyze_video(db, "v1", FakeProvider(dup), "m")

    big = recipe_payload(ev.id)
    big["tags"] = [
        {"value": f"t{i}", "evidence_id": ev.id, "quote": "flour"}
        for i in range(201)
    ]
    with pytest.raises(AnalysisError):
        analyze_video(db, "v1", FakeProvider(big), "m")


def test_evidence_too_large_refused(tmp_path, monkeypatch):
    db, store, evidence = make_env(tmp_path)
    monkeypatch.setattr(analysis, "_MAX_EVIDENCE_CHARS", 5)
    with pytest.raises(AnalysisError):
        analyze_video(db, "v1", FakeProvider(None), "m")


def test_unknown_top_level_keys_rejected(tmp_path):
    db, store, evidence = make_env(tmp_path)
    ev = evidence[0]
    payload = recipe_payload(ev.id)
    payload["id"] = "injected-id"
    with pytest.raises(AnalysisError):
        analyze_video(db, "v1", FakeProvider(payload), "m")


def test_falsy_list_types_rejected(tmp_path):
    db, store, evidence = make_env(tmp_path)
    ev = evidence[0]
    payload = recipe_payload(ev.id)
    payload["ingredients"] = ""
    with pytest.raises(AnalysisError):
        analyze_video(db, "v1", FakeProvider(payload), "m")


def test_whitespace_fact_fields_rejected(tmp_path):
    db, store, evidence = make_env(tmp_path)
    ev = evidence[0]
    for field in ("value", "quote", "evidence_id"):
        payload = recipe_payload(ev.id)
        payload["ingredients"][0][field] = "   "
        with pytest.raises(AnalysisError):
            analyze_video(db, "v1", FakeProvider(payload), "m")


def test_stale_evidence_excluded_from_categories(tmp_path):
    db, store, evidence = make_env(tmp_path)
    ev = evidence[0]
    analyze_video(db, "v1", FakeProvider(recipe_payload(ev.id)), "m")
    assert category_candidates(db) != []
    store.add_evidence("v1", "brand new content", "notes", "me")
    assert category_candidates(db) == []


def test_category_labels_derived_from_tags(tmp_path):
    db, store, evidence = make_env(tmp_path)
    ev = evidence[0]
    analyze_video(db, "v1", FakeProvider(recipe_payload(ev.id)), "m")
    candidates = category_candidates(db)
    assert len(candidates) == 1
    assert candidates[0]["label"] == "baking"
    assert candidates[0]["video_ids"] == ["v1"]
    assert candidates[0]["evidence_ids"] == [ev.id]
    assert candidates[0]["proposed"] is True


def test_non_recipe_tags_excluded_from_categories(tmp_path):
    db, store, evidence = make_env(tmp_path)
    ev = evidence[0]
    payload = {
        "is_recipe": False,
        "tags": [{"value": "baking", "evidence_id": ev.id, "quote": "flour"}],
    }
    save_analysis(db, "v1", payload, "m")
    assert category_candidates(db) == []


def test_inactive_videos_excluded_from_categories(tmp_path):
    db, store, evidence = make_env(tmp_path, video_ids=("v1", "v2"))
    ev1 = evidence[0]
    ev2 = store.add_evidence("v2", "mix water with flour.", "notes", "n")
    analyze_video(db, "v1", FakeProvider(recipe_payload(ev1.id)), "m")
    payload2 = {
        "is_recipe": True,
        "title": {"value": "Water", "evidence_id": ev2.id, "quote": "water"},
        "ingredients": [{"value": "water", "evidence_id": ev2.id, "quote": "water"}],
        "steps": [],
        "tags": [{"value": "beverage", "evidence_id": ev2.id, "quote": "water"}],
    }
    analyze_video(db, "v2", FakeProvider(payload2), "m")
    db._conn.execute(
        "UPDATE playlist_items SET removed_at = '2026-01-01T00:00:00+00:00' "
        "WHERE video_id = 'v2'"
    )
    db._conn.commit()
    labels = {c["label"] for c in category_candidates(db)}
    assert "baking" in labels
    assert "beverage" not in labels


def test_list_analyses_fails_closed_on_tampered_id(tmp_path):
    db, store, evidence = make_env(tmp_path)
    ev = evidence[0]
    a = analyze_video(db, "v1", FakeProvider(recipe_payload(ev.id)), "m")
    db._conn.execute("UPDATE recipe_analyses SET id = 'tampered' WHERE id = ?", (a.id,))
    db._conn.commit()
    with pytest.raises(AnalysisError):
        list_analyses(db, "v1")


def test_list_analyses_fails_closed_on_corrupt_json(tmp_path):
    db, store, evidence = make_env(tmp_path)
    ev = evidence[0]
    a = analyze_video(db, "v1", FakeProvider(recipe_payload(ev.id)), "m")
    db._conn.execute(
        "UPDATE recipe_analyses SET data_json = 'not json' WHERE id = ?", (a.id,)
    )
    db._conn.commit()
    with pytest.raises(AnalysisError):
        list_analyses(db, "v1")


def test_list_analyses_fails_closed_on_video_mismatch(tmp_path):
    db, store, evidence = make_env(tmp_path)
    ev = evidence[0]
    a = analyze_video(db, "v1", FakeProvider(recipe_payload(ev.id)), "m")
    row = db._conn.execute(
        "SELECT data_json FROM recipe_analyses WHERE id = ?", (a.id,)
    ).fetchone()
    data = json.loads(row["data_json"])
    data["video_id"] = "other"
    db._conn.execute(
        "UPDATE recipe_analyses SET data_json = ? WHERE id = ?",
        (json.dumps(data), a.id),
    )
    db._conn.commit()
    with pytest.raises(AnalysisError):
        list_analyses(db, "v1")


def test_prompt_grounds_in_evidence_not_metadata(tmp_path):
    db, store, evidence = make_env(tmp_path)
    ev = evidence[0]
    provider = FakeProvider(recipe_payload(ev.id))
    analyze_video(db, "v1", provider, "m")
    messages = json.dumps(provider.calls[0][0])
    assert "Video v1" not in messages
    assert evidence[0].content in messages

def test_analysis_schema_constraints():
    schema = analysis._ANALYSIS_SCHEMA
    assert schema["required"] == ["is_recipe"]
    assert schema["properties"]["is_recipe"] == {"type": "boolean"}
    assert schema["properties"]["ingredients"]["maxItems"] == 200
    assert schema["properties"]["steps"]["maxItems"] == 200
    assert schema["properties"]["tags"]["maxItems"] == 200

    fact = analysis._FACT_SCHEMA
    assert fact["required"] == ["value", "evidence_id", "quote"]
    assert fact["additionalProperties"] is False
    assert schema["properties"]["title"]["anyOf"][0] is fact
    assert schema["properties"]["title"]["anyOf"][1] == {"type": "null"}
    assert schema["properties"]["ingredients"]["items"] is fact
