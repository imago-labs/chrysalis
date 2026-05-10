# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at the root of this repository
# or at http://www.apache.org/licenses/LICENSE-2.0
"""
MEMOIR Audit Log
----------------
Immutable SQLite-backed audit trail.

Every memory operation is recorded here: approved writes, flagged writes,
rejected writes, reads, and stale detections. Records are never modified
or deleted. The full chain from agent input → tool call → memory write
is queryable by session, tag, key, or time window.

This is the SAF Continuous Observability pillar made concrete.
When something goes wrong, reconstruction is possible.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, List, Optional

from memoir.models.memory import AuditRecord, CritiqueVerdict, EpistemicTag


# ---------------------------------------------------------------------------
# Default DB path
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = Path.home() / ".memoir" / "audit.db"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

CREATE_AUDIT_TABLE = """
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id             TEXT PRIMARY KEY,
    entry_id             TEXT NOT NULL,
    session_id           TEXT NOT NULL,
    key                  TEXT NOT NULL,
    content              TEXT,
    operation            TEXT NOT NULL,
    epistemic_tag        TEXT NOT NULL,
    critique_verdict     TEXT NOT NULL,
    critique_notes       TEXT,
    source_reference     TEXT,
    tool_call_id         TEXT,
    human_session_context TEXT,
    recorded_at          TEXT NOT NULL,
    metadata             TEXT NOT NULL DEFAULT '{}'
);
"""

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_session ON audit_log(session_id);",
    "CREATE INDEX IF NOT EXISTS idx_key ON audit_log(key);",
    "CREATE INDEX IF NOT EXISTS idx_tag ON audit_log(epistemic_tag);",
    "CREATE INDEX IF NOT EXISTS idx_recorded_at ON audit_log(recorded_at);",
]


# ---------------------------------------------------------------------------
# AuditLogger
# ---------------------------------------------------------------------------

class AuditLogger:
    """
    SQLite-backed immutable audit logger.

    Thread-safe via connection-per-call pattern.
    Records are INSERT-only — no UPDATE or DELETE operations.
    """

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = Path(db_path or os.environ.get("MEMOIR_DB_PATH", DEFAULT_DB_PATH))
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def record(self, audit_record: AuditRecord) -> None:
        """Write an AuditRecord to the log. Uses INSERT OR REPLACE so the
        chain pipeline can update a record's metadata with tx signature
        after the initial write."""
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO audit_log (
                    audit_id, entry_id, session_id, key, content, operation,
                    epistemic_tag, critique_verdict, critique_notes,
                    source_reference, tool_call_id, human_session_context,
                    recorded_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_record.audit_id,
                    audit_record.entry_id,
                    audit_record.session_id,
                    audit_record.key,
                    audit_record.content,
                    audit_record.operation,
                    audit_record.epistemic_tag.value,
                    audit_record.critique_verdict.value,
                    audit_record.critique_notes,
                    audit_record.source_reference,
                    audit_record.tool_call_id,
                    audit_record.human_session_context,
                    audit_record.recorded_at.isoformat(),
                    json.dumps(audit_record.metadata),
                ),
            )

    def query(
        self,
        session_id: Optional[str] = None,
        key: Optional[str] = None,
        epistemic_tag: Optional[EpistemicTag] = None,
        critique_verdict: Optional[CritiqueVerdict] = None,
        after: Optional[datetime] = None,
        before: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[AuditRecord]:
        """
        Query the audit log with optional filters.

        All filters are ANDed together.
        Results are ordered by recorded_at descending (most recent first).
        """
        conditions = []
        params: list = []

        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if key:
            conditions.append("key = ?")
            params.append(key)
        if epistemic_tag:
            conditions.append("epistemic_tag = ?")
            params.append(epistemic_tag.value)
        if critique_verdict:
            conditions.append("critique_verdict = ?")
            params.append(critique_verdict.value)
        if after:
            conditions.append("recorded_at >= ?")
            params.append(after.isoformat())
        if before:
            conditions.append("recorded_at <= ?")
            params.append(before.isoformat())

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.extend([limit, offset])

        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM audit_log
                {where}
                ORDER BY recorded_at DESC
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()

        return [self._row_to_record(row) for row in rows]

    def get_session_summary(self, session_id: str) -> dict:
        """Return a summary of all operations for a session."""
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    epistemic_tag,
                    critique_verdict,
                    COUNT(*) as count
                FROM audit_log
                WHERE session_id = ?
                GROUP BY epistemic_tag, critique_verdict
                """,
                (session_id,),
            ).fetchall()

        summary: dict = {
            "session_id": session_id,
            "total": 0,
            "by_tag": {},
            "by_verdict": {},
            "approved": 0,
            "flagged": 0,
            "rejected": 0,
        }

        for row in rows:
            tag, verdict, count = row["epistemic_tag"], row["critique_verdict"], row["count"]
            summary["total"] += count
            summary["by_tag"][tag] = summary["by_tag"].get(tag, 0) + count
            summary["by_verdict"][verdict] = summary["by_verdict"].get(verdict, 0) + count
            if verdict == "PASS":
                summary["approved"] += count
            elif verdict == "FLAG":
                summary["flagged"] += count
            elif verdict == "REJECT":
                summary["rejected"] += count

        return summary

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._connection() as conn:
            conn.execute(CREATE_AUDIT_TABLE)
            for idx in CREATE_INDEXES:
                conn.execute(idx)
            # Migration: add content column if DB was created before it existed
            try:
                conn.execute("ALTER TABLE audit_log ADD COLUMN content TEXT")
            except Exception:
                pass  # column already exists

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")  # Better concurrency
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _row_to_record(self, row: sqlite3.Row) -> AuditRecord:
        # content column may not exist in older databases
        try:
            content = row["content"]
        except (IndexError, KeyError):
            content = None
        return AuditRecord(
            audit_id=row["audit_id"],
            entry_id=row["entry_id"],
            session_id=row["session_id"],
            key=row["key"],
            content=content,
            operation=row["operation"],
            epistemic_tag=EpistemicTag(row["epistemic_tag"]),
            critique_verdict=CritiqueVerdict(row["critique_verdict"]),
            critique_notes=row["critique_notes"],
            source_reference=row["source_reference"],
            tool_call_id=row["tool_call_id"],
            human_session_context=row["human_session_context"],
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
            metadata=json.loads(row["metadata"]),
        )
