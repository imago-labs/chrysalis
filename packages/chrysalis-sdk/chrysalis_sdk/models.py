# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
"""
Pydantic models that mirror the Chrysalis platform HTTP API response shapes.

The SDK keeps its own copy of the wire schemas instead of importing them
from the platform so the developer-facing package stays small and free of
server-only dependencies.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ValidationResult(BaseModel):
    """Outcome of running a belief through the MEMOIR pipeline."""

    entry_id: str
    approved: bool
    epistemic_tag: str
    critique_verdict: str
    message: str = ""
    belief_quality_score: float | None = None
    conflict_detected: bool = False
    conflict_details: str | None = None


class Belief(BaseModel):
    """A single belief stored by the platform."""

    entry_id: str
    session_id: str
    key: str
    content: str
    epistemic_tag: str
    source_reference: str | None = None
    recorded_at: datetime


class AuditRecord(BaseModel):
    """A row from the audit log."""

    entry_id: str
    session_id: str
    key: str
    content: str
    epistemic_tag: str
    critique_verdict: str
    recorded_at: datetime
    attestation_tx: str | None = None


class GovernanceEvent(BaseModel):
    """An event yielded by the SSE governance stream."""

    type: str
    entry_id: str | None = None
    key: str | None = None
    epistemic_tag: str | None = None
    critique_verdict: str | None = None
    approved: bool | None = None
    timestamp: datetime | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class SessionSummary(BaseModel):
    """High-level metrics for a single session."""

    session_id: str
    belief_count: int
    approved_count: int
    flagged_count: int
    rejected_count: int
    conflict_count: int = 0
    last_activity: datetime | None = None


class TrustScore(BaseModel):
    """Trust score for an agent or session."""

    agent_id: str
    score: float
    sample_size: int
    last_updated: datetime | None = None
