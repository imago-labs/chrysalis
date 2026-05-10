# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at the root of this repository
# or at http://www.apache.org/licenses/LICENSE-2.0
"""
Derivation Graph (SQLite-backed)
---------------------------------
Tracks parent -> child relationships between beliefs across agent boundaries.
When Agent A produces a belief that Agent B inherits, we store that link
so we can propagate quality signals downstream.

I'm using SQLite here (same pattern as the audit logger) because we need
this to survive process restarts and be queryable efficiently.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

CREATE_DERIVATION_TABLE = """
CREATE TABLE IF NOT EXISTS derivation_graph (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_attestation_id   TEXT NOT NULL,
    child_attestation_id    TEXT NOT NULL,
    parent_agent_id         TEXT NOT NULL,
    child_agent_id          TEXT NOT NULL,
    parent_entry_id         TEXT NOT NULL,
    child_entry_id          TEXT NOT NULL,
    original_bqs            REAL NOT NULL,
    discounted_bqs          REAL NOT NULL,
    handoff_count           INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    UNIQUE(parent_attestation_id, child_attestation_id)
);
"""

CREATE_DERIVATION_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_parent_attest ON derivation_graph(parent_attestation_id);",
    "CREATE INDEX IF NOT EXISTS idx_child_attest ON derivation_graph(child_attestation_id);",
    "CREATE INDEX IF NOT EXISTS idx_parent_agent ON derivation_graph(parent_agent_id);",
    "CREATE INDEX IF NOT EXISTS idx_child_entry ON derivation_graph(child_entry_id);",
]


class DerivationEdge(BaseModel):
    """A single edge in the derivation graph."""
    model_config = ConfigDict(extra="forbid")

    parent_attestation_id: str
    child_attestation_id: str
    parent_agent_id: str
    child_agent_id: str
    parent_entry_id: str
    child_entry_id: str
    original_bqs: float
    discounted_bqs: float
    handoff_count: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


DEFAULT_GRAPH_DB_PATH = Path.home() / ".memoir" / "derivation.db"


class DerivationGraph:
    """
    SQLite-backed derivation graph for cross-agent belief provenance.
    Same connection pattern as AuditLogger: connection-per-call for thread safety.
    """

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = Path(db_path or os.environ.get("MEMOIR_DERIVATION_DB_PATH", DEFAULT_GRAPH_DB_PATH))
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def add_edge(self, edge: DerivationEdge) -> None:
        """Record a parent -> child derivation link."""
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO derivation_graph (
                    parent_attestation_id, child_attestation_id,
                    parent_agent_id, child_agent_id,
                    parent_entry_id, child_entry_id,
                    original_bqs, discounted_bqs,
                    handoff_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    edge.parent_attestation_id,
                    edge.child_attestation_id,
                    edge.parent_agent_id,
                    edge.child_agent_id,
                    edge.parent_entry_id,
                    edge.child_entry_id,
                    edge.original_bqs,
                    edge.discounted_bqs,
                    edge.handoff_count,
                    edge.created_at.isoformat(),
                ),
            )

    def get_children(self, parent_attestation_id: str) -> list[DerivationEdge]:
        """Find all beliefs derived from a parent attestation."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM derivation_graph WHERE parent_attestation_id = ?",
                (parent_attestation_id,),
            ).fetchall()
        return [self._row_to_edge(r) for r in rows]

    def get_parents(self, child_attestation_id: str) -> list[DerivationEdge]:
        """Find all parent beliefs for a derived belief."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM derivation_graph WHERE child_attestation_id = ?",
                (child_attestation_id,),
            ).fetchall()
        return [self._row_to_edge(r) for r in rows]

    def get_all_descendants(self, parent_attestation_id: str) -> list[DerivationEdge]:
        """
        BFS traversal to find all downstream derivatives of a belief.
        This is what we call when a parent belief gets rejected and
        we need to queue all children for re-evaluation.
        """
        visited: set[str] = set()
        queue = [parent_attestation_id]
        descendants: list[DerivationEdge] = []

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            children = self.get_children(current)
            for child in children:
                descendants.append(child)
                queue.append(child.child_attestation_id)

        return descendants

    def get_agent_derivations(self, agent_id: str) -> list[DerivationEdge]:
        """Get all derivation edges for a specific agent."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM derivation_graph WHERE child_agent_id = ?",
                (agent_id,),
            ).fetchall()
        return [self._row_to_edge(r) for r in rows]

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._connection() as conn:
            conn.execute(CREATE_DERIVATION_TABLE)
            for idx in CREATE_DERIVATION_INDEXES:
                conn.execute(idx)

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _row_to_edge(self, row: sqlite3.Row) -> DerivationEdge:
        return DerivationEdge(
            parent_attestation_id=row["parent_attestation_id"],
            child_attestation_id=row["child_attestation_id"],
            parent_agent_id=row["parent_agent_id"],
            child_agent_id=row["child_agent_id"],
            parent_entry_id=row["parent_entry_id"],
            child_entry_id=row["child_entry_id"],
            original_bqs=row["original_bqs"],
            discounted_bqs=row["discounted_bqs"],
            handoff_count=row["handoff_count"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
