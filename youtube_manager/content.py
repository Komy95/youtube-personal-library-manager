"""Phase 2 content evidence capture (bounded foundation only)."""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from youtube_manager.db import Database

EvidenceKind = Literal["transcript", "visual_notes", "notes"]

_MAX_CONTENT_CHARS = 500_000
_MAX_FILE_BYTES = 2 * 1024 * 1024
_ALLOWED_SUFFIXES = {".txt", ".srt", ".vtt"}

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_TIMING_LINE_RE = re.compile(
    r"^(?:\d{1,2}:\d{2}:\d{2}|\d{1,2}:\d{2})[.,]\d{3}\s*-->"
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _evidence_id(
    video_id: str, kind: str, content: str, source: str, language: str | None
) -> str:
    payload = [video_id, kind, content, source, language or ""]
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return _sha256(canonical)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_id: str = Field(min_length=1)
    kind: EvidenceKind
    content: str = Field(min_length=1, max_length=_MAX_CONTENT_CHARS)
    source: str = Field(min_length=1, max_length=500)
    language: str | None = Field(default=None, max_length=32)
    id: str = ""
    content_hash: str = ""
    created_at: str = ""

    @field_validator("video_id", "source", "content", mode="after")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty or whitespace-only")
        return value

    @model_validator(mode="after")
    def _compute_derived(self) -> "Evidence":
        self.content_hash = _sha256(self.content)
        if not self.created_at:
            self.created_at = _now_utc()
        self.id = _evidence_id(
            self.video_id, self.kind, self.content, self.source, self.language
        )
        return self

    @staticmethod
    def compute_id(
        video_id: str, kind: str, content: str, source: str, language: str | None
    ) -> str:
        return _evidence_id(video_id, kind, content, source, language)


def _clean_line(line: str) -> str:
    line = _HTML_TAG_RE.sub("", line)
    return html.unescape(line)


def _clean_text(text: str) -> str:
    return html.unescape(text)


def _parse_subtitles(raw: str) -> str:
    lines = raw.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    if lines and lines[0].strip().upper().startswith("WEBVTT"):
        i = 1
    in_cue = False
    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            in_cue = False
            i += 1
            continue
        if _TIMING_LINE_RE.match(stripped):
            in_cue = True
            i += 1
            continue
        if in_cue:
            out.append(_clean_line(stripped))
            i += 1
            continue
        upper = stripped.upper()
        if upper.startswith("NOTE") or upper.startswith("STYLE") or upper.startswith("REGION"):
            i += 1
            while i < n and lines[i].strip():
                i += 1
            continue
        i += 1
    return "\n".join(out)


class EvidenceStore:
    """Persists immutable content evidence against known videos."""

    def __init__(self, db: Database):
        self._db = db
        self._conn = db._conn

    def _check_video(self, video_id: str) -> None:
        row = self._conn.execute(
            "SELECT 1 FROM videos WHERE id = ?", (video_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown video_id: {video_id}")

    def _store(self, ev: Evidence) -> Evidence:
        self._check_video(ev.video_id)
        existing = self._conn.execute(
            "SELECT id, video_id, kind, content, source, language, content_hash, "
            "created_at FROM content_evidence WHERE id = ?",
            (ev.id,),
        ).fetchone()
        if existing is not None:
            return self._row_to_evidence(existing)
        with self._conn:
            self._conn.execute(
                "INSERT INTO content_evidence "
                "(id, video_id, kind, content, source, language, content_hash, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ev.id,
                    ev.video_id,
                    ev.kind,
                    ev.content,
                    ev.source,
                    ev.language,
                    ev.content_hash,
                    ev.created_at,
                ),
            )
        return ev

    @staticmethod
    def _row_to_evidence(r) -> Evidence:
        ev = Evidence(
            video_id=r["video_id"],
            kind=r["kind"],
            content=r["content"],
            source=r["source"],
            language=r["language"],
            created_at=r["created_at"],
        )
        if ev.id != r["id"] or ev.content_hash != r["content_hash"]:
            raise ValueError("content_evidence row failed integrity check")
        return ev

    def add_evidence(
        self,
        video_id: str,
        text: str,
        kind: str,
        source: str,
        language: str | None = None,
    ) -> Evidence:
        ev = Evidence(
            video_id=video_id,
            kind=kind,
            content=text,
            source=source,
            language=language,
        )
        return self._store(ev)

    def import_text(
        self,
        video_id: str,
        path: str | Path,
        kind: str = "transcript",
        source: str | None = None,
        language: str | None = None,
    ) -> Evidence:
        p = Path(path)
        if p.suffix.lower() not in _ALLOWED_SUFFIXES:
            raise ValueError(
                f"unsupported file type {p.suffix!r}; expected .txt, .srt, or .vtt"
            )
        with p.open("rb") as f:
            data = f.read(_MAX_FILE_BYTES + 1)
        if len(data) > _MAX_FILE_BYTES:
            raise ValueError(f"file too large: exceeds {_MAX_FILE_BYTES} bytes")
        raw = data.decode("utf-8-sig")
        if p.suffix.lower() == ".txt":
            text = _clean_text(raw)
        else:
            text = _parse_subtitles(raw)
        if not text.strip():
            raise ValueError("no spoken text found")
        resolved_source = source or p.name
        ev = Evidence(
            video_id=video_id,
            kind=kind,
            content=text,
            source=resolved_source,
            language=language,
        )
        return self._store(ev)

    def list_evidence(self, video_id: str | None = None) -> list[Evidence]:
        if video_id is None:
            rows = self._conn.execute(
                "SELECT id, video_id, kind, content, source, language, content_hash, "
                "created_at FROM content_evidence ORDER BY created_at, rowid"
            )
        else:
            rows = self._conn.execute(
                "SELECT id, video_id, kind, content, source, language, content_hash, "
                "created_at FROM content_evidence WHERE video_id = ? "
                "ORDER BY created_at, rowid",
                (video_id,),
            )
        return [self._row_to_evidence(r) for r in rows]

    def coverage(self) -> dict:
        rows = self._conn.execute(
            "SELECT DISTINCT video_id FROM playlist_items WHERE removed_at IS NULL"
        ).fetchall()
        total = len(rows)
        if total == 0:
            return {"totalVideos": 0, "withEvidence": 0, "withoutEvidence": 0}
        placeholders = ",".join("?" for _ in rows)
        with_count = self._conn.execute(
            f"SELECT COUNT(DISTINCT video_id) FROM content_evidence "
            f"WHERE video_id IN ({placeholders})",
            [r["video_id"] for r in rows],
        ).fetchone()[0]
        return {
            "totalVideos": total,
            "withEvidence": with_count,
            "withoutEvidence": total - with_count,
        }
