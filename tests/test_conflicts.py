"""
Belief Conflict Detection Tests
---------------------------------
Tests for conflict detection, resolution strategies, and edge cases.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memoir.models.memory import (
    AuditRecord,
    ConflictRecord,
    ConflictType,
    CritiqueVerdict,
    EpistemicTag,
    ResolutionStrategy,
)
from memoir.conflicts.detector import (
    detect_conflicts,
    detect_rule_violations,
)
from memoir.conflicts.models import ConflictCandidate
from memoir.conflicts.resolver import resolve_conflict, resolve_all


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_record(
    key: str = "test_key",
    tag: EpistemicTag = EpistemicTag.VERIFIED,
    verdict: CritiqueVerdict = CritiqueVerdict.PASS,
    entry_id: str = "existing-1",
    critique_notes: str | None = None,
    source: str | None = "src/main.py",
) -> AuditRecord:
    return AuditRecord(
        entry_id=entry_id,
        session_id="conflict-test",
        key=key,
        operation="WRITE_APPROVED",
        epistemic_tag=tag,
        critique_verdict=verdict,
        critique_notes=critique_notes,
        source_reference=source,
    )


# ---------------------------------------------------------------------------
# Detector Tests
# ---------------------------------------------------------------------------

class TestConflictDetector:

    def test_no_conflicts_unrelated(self):
        existing = [make_record(key="weather_forecast")]
        conflicts = detect_conflicts(
            new_entry_id="new-1",
            new_key="stock_price",
            new_content="AAPL is at $150",
            new_tag="VERIFIED",
            existing_records=existing,
        )
        assert len(conflicts) == 0

    def test_temporal_conflict_with_stale(self):
        existing = [make_record(
            key="sol_price",
            tag=EpistemicTag.STALE,
            entry_id="stale-1",
        )]
        conflicts = detect_conflicts(
            new_entry_id="new-1",
            new_key="sol_price",
            new_content="SOL is at $180 now",
            new_tag="VERIFIED",
            existing_records=existing,
        )
        assert any(c.conflict_type == ConflictType.TEMPORAL_CONFLICT for c in conflicts)

    def test_source_disagreement_same_key(self):
        # Same key with VERIFIED existing and INFERRED new should trigger
        # source disagreement when there's enough content overlap
        existing = [make_record(
            key="market_trend",
            tag=EpistemicTag.VERIFIED,
            entry_id="existing-1",
            critique_notes="Market trend is bullish based on volume data, will rise and increase",
        )]
        conflicts = detect_conflicts(
            new_entry_id="new-1",
            new_key="market_trend",
            new_content="Market trend analysis from different source shows market will decline and fall",
            new_tag="INFERRED",
            existing_records=existing,
            similarity_threshold=0.1,
        )
        # Should detect either source disagreement or direct contradiction
        # (positive "rise" vs negative "decline/fall" on same key with overlap)
        assert len(conflicts) > 0

    def test_empty_existing_records(self):
        conflicts = detect_conflicts(
            new_entry_id="new-1",
            new_key="anything",
            new_content="Some belief",
            new_tag="VERIFIED",
            existing_records=[],
        )
        assert len(conflicts) == 0

    def test_detect_rule_violations(self):
        user_rules = [make_record(
            key="trading_rule",
            tag=EpistemicTag.VERIFIED,
            critique_notes="Never sell during a dip. Diamond hands strategy.",
        )]
        violations = detect_rule_violations(
            new_content="Selling SOL position due to dip in market, sell during this dip to cut losses",
            new_tag="INFERRED",
            user_stated_records=user_rules,
        )
        assert len(violations) > 0
        assert violations[0].conflict_type == ConflictType.RULE_VIOLATION


# ---------------------------------------------------------------------------
# Resolver Tests
# ---------------------------------------------------------------------------

class TestConflictResolver:

    def test_rule_violation_user_stated_wins(self):
        candidate = ConflictCandidate(
            belief_a_id="new-1",
            belief_a_key="action",
            belief_a_content="Sell now",
            belief_a_tag="INFERRED",
            belief_b_id="rule-1",
            belief_b_key="rule",
            belief_b_content="Never sell during dip",
            belief_b_tag="ASSUMED",
            conflict_type=ConflictType.RULE_VIOLATION,
            description="test",
        )
        record = resolve_conflict(candidate, "test-session")
        assert record.resolution_strategy == ResolutionStrategy.USER_STATED_PRIORITY
        assert record.winner_id == "rule-1"

    def test_rule_violation_verified_overrides(self):
        candidate = ConflictCandidate(
            belief_a_id="new-1",
            belief_a_key="stop_loss",
            belief_a_content="Stop-loss triggered",
            belief_a_tag="VERIFIED",
            belief_b_id="rule-1",
            belief_b_key="rule",
            belief_b_content="Never sell",
            belief_b_tag="ASSUMED",
            conflict_type=ConflictType.RULE_VIOLATION,
            description="test",
        )
        record = resolve_conflict(candidate, "test-session")
        assert record.resolution_strategy == ResolutionStrategy.NEWER_VERIFIED_WINS
        assert record.winner_id == "new-1"

    def test_direct_contradiction_with_bqs(self):
        candidate = ConflictCandidate(
            belief_a_id="a",
            belief_a_key="price",
            belief_a_content="SOL up",
            belief_a_tag="VERIFIED",
            belief_a_bqs=0.85,
            belief_b_id="b",
            belief_b_key="price",
            belief_b_content="SOL down",
            belief_b_tag="INFERRED",
            belief_b_bqs=0.45,
            conflict_type=ConflictType.DIRECT_CONTRADICTION,
            description="test",
        )
        record = resolve_conflict(candidate, "test-session")
        assert record.resolution_strategy == ResolutionStrategy.HIGHER_BQS_WINS
        assert record.winner_id == "a"

    def test_direct_contradiction_no_bqs_escalates(self):
        candidate = ConflictCandidate(
            belief_a_id="a",
            belief_a_key="x",
            belief_a_content="yes",
            belief_a_tag="VERIFIED",
            belief_b_id="b",
            belief_b_key="x",
            belief_b_content="no",
            belief_b_tag="VERIFIED",
            conflict_type=ConflictType.DIRECT_CONTRADICTION,
            description="test",
        )
        record = resolve_conflict(candidate, "test-session")
        assert record.resolution_strategy == ResolutionStrategy.ESCALATE_TO_HUMAN
        assert record.winner_id is None

    def test_temporal_conflict_newer_wins(self):
        candidate = ConflictCandidate(
            belief_a_id="new",
            belief_a_key="data",
            belief_a_content="new data",
            belief_a_tag="VERIFIED",
            belief_b_id="old",
            belief_b_key="data",
            belief_b_content="old data",
            belief_b_tag="STALE",
            conflict_type=ConflictType.TEMPORAL_CONFLICT,
            description="test",
        )
        record = resolve_conflict(candidate, "test-session")
        assert record.resolution_strategy == ResolutionStrategy.NEWER_VERIFIED_WINS
        assert record.winner_id == "new"

    def test_resolve_all_batch(self):
        candidates = [
            ConflictCandidate(
                belief_a_id=f"a{i}", belief_a_key="k", belief_a_content="c",
                belief_a_tag="VERIFIED",
                belief_b_id=f"b{i}", belief_b_key="k", belief_b_content="c",
                belief_b_tag="STALE",
                conflict_type=ConflictType.TEMPORAL_CONFLICT,
                description="test",
            )
            for i in range(3)
        ]
        records = resolve_all(candidates, "batch-test")
        assert len(records) == 3
        assert all(isinstance(r, ConflictRecord) for r in records)

    def test_conflict_record_model(self):
        record = ConflictRecord(
            session_id="s1",
            belief_a_id="a",
            belief_b_id="b",
            conflict_type=ConflictType.DIRECT_CONTRADICTION,
            resolution_strategy=ResolutionStrategy.ESCALATE_TO_HUMAN,
            description="test conflict",
        )
        assert record.conflict_id  # auto-generated
        assert not record.resolved
