# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at the root of this repository
# or at http://www.apache.org/licenses/LICENSE-2.0
"""
ORACLE Engine Tests
--------------------
Tests for BQS computation, pattern analysis, insight generation,
and the full learning loop. All LLM calls mocked.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from memoir.core.audit import AuditLogger
from memoir.models.memory import (
    AuditRecord,
    BQSScore,
    CritiqueVerdict,
    EpistemicTag,
    MetacognitiveInsight,
    OracleReport,
)
from memoir.oracle.bqs import (
    classify_source_type,
    compute_bqs,
    compute_critique_concordance,
    compute_source_reliability,
    compute_tag_confidence,
    compute_temporal_freshness,
    compute_verification_depth,
)
from memoir.oracle.analyzer import (
    compute_all_bqs,
    compute_cycle_id,
    detect_patterns,
    compute_source_weight_updates,
    build_report,
)
from memoir.oracle.insights import generate_insights_from_patterns
from memoir.oracle.learning_loop import OracleLearningLoop


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test_oracle_audit.db")


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


def make_audit_record(
    session_id: str = "oracle-test",
    key: str = "test_key",
    tag: EpistemicTag = EpistemicTag.VERIFIED,
    verdict: CritiqueVerdict = CritiqueVerdict.PASS,
    source_reference: str | None = "src/main.py",
    entry_id: str = "entry-1",
    metadata: dict | None = None,
    recorded_at: datetime | None = None,
) -> AuditRecord:
    return AuditRecord(
        entry_id=entry_id,
        session_id=session_id,
        key=key,
        operation="WRITE_APPROVED",
        epistemic_tag=tag,
        critique_verdict=verdict,
        source_reference=source_reference,
        metadata=metadata or {"had_source_context": True, "had_existing_memory": False},
        recorded_at=recorded_at or datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# BQS Tests
# ---------------------------------------------------------------------------

class TestBQSComputation:

    def test_classify_source_type_file(self):
        assert classify_source_type("src/main.py") == "file"

    def test_classify_source_type_url(self):
        assert classify_source_type("https://example.com") == "url"

    def test_classify_source_type_none(self):
        assert classify_source_type(None) == "none"

    def test_source_reliability_with_custom_weights(self):
        weights = {"src/main.py": 0.95}
        score = compute_source_reliability("src/main.py", source_weights=weights)
        assert score == 0.95

    def test_source_reliability_default(self):
        score = compute_source_reliability("src/main.py")
        assert score == 0.85  # file default

    def test_verification_depth_with_source_context(self):
        record = make_audit_record(metadata={"had_source_context": True, "had_existing_memory": True})
        depth = compute_verification_depth(record)
        assert depth == 1.0  # 0.5 + 0.2 + 0.3

    def test_verification_depth_no_context(self):
        record = make_audit_record(
            tag=EpistemicTag.ASSUMED,
            metadata={"had_source_context": False, "had_existing_memory": False},
        )
        depth = compute_verification_depth(record)
        assert depth == 0.0

    def test_tag_confidence_verified_pass(self):
        record = make_audit_record(tag=EpistemicTag.VERIFIED, verdict=CritiqueVerdict.PASS)
        confidence = compute_tag_confidence(record)
        assert confidence == 0.95

    def test_tag_confidence_assumed_reject(self):
        record = make_audit_record(tag=EpistemicTag.ASSUMED, verdict=CritiqueVerdict.REJECT)
        confidence = compute_tag_confidence(record)
        assert confidence == 0.25

    def test_temporal_freshness_just_recorded(self):
        now = datetime.now(timezone.utc)
        record = make_audit_record(recorded_at=now)
        freshness = compute_temporal_freshness(record, now=now)
        assert freshness == 1.0

    def test_temporal_freshness_old_record(self):
        now = datetime.now(timezone.utc)
        record = make_audit_record(recorded_at=now - timedelta(hours=25))
        freshness = compute_temporal_freshness(record, now=now)
        assert freshness == 0.1

    def test_temporal_freshness_stale_tag(self):
        record = make_audit_record(tag=EpistemicTag.STALE)
        freshness = compute_temporal_freshness(record)
        assert freshness == 0.0

    def test_critique_concordance_perfect(self):
        record = make_audit_record(tag=EpistemicTag.VERIFIED, verdict=CritiqueVerdict.PASS)
        concordance = compute_critique_concordance(record)
        assert concordance == 1.0

    def test_critique_concordance_disagreement(self):
        record = make_audit_record(tag=EpistemicTag.VERIFIED, verdict=CritiqueVerdict.REJECT)
        concordance = compute_critique_concordance(record)
        assert concordance == 0.1

    def test_full_bqs_computation(self):
        record = make_audit_record()
        bqs = compute_bqs(record)
        assert isinstance(bqs, BQSScore)
        assert 0.0 <= bqs.composite_score <= 1.0
        assert bqs.entry_id == "entry-1"

    def test_bqs_score_model_compute(self):
        bqs = BQSScore.compute(
            entry_id="e1", session_id="s1",
            source_reliability=0.8, verification_depth=0.7,
            tag_confidence=0.9, temporal_freshness=1.0,
            critique_concordance=0.8,
        )
        # 0.25*0.8 + 0.20*0.7 + 0.20*0.9 + 0.20*1.0 + 0.15*0.8
        expected = 0.25 * 0.8 + 0.20 * 0.7 + 0.20 * 0.9 + 0.20 * 1.0 + 0.15 * 0.8
        assert abs(bqs.composite_score - round(expected, 4)) < 0.001


# ---------------------------------------------------------------------------
# Analyzer Tests
# ---------------------------------------------------------------------------

class TestAnalyzer:

    def test_cycle_id_deterministic(self):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, tzinfo=timezone.utc)
        id1 = compute_cycle_id("s1", start, end)
        id2 = compute_cycle_id("s1", start, end)
        assert id1 == id2

    def test_cycle_id_different_sessions(self):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, tzinfo=timezone.utc)
        id1 = compute_cycle_id("s1", start, end)
        id2 = compute_cycle_id("s2", start, end)
        assert id1 != id2

    def test_compute_all_bqs(self):
        records = [make_audit_record(entry_id=f"e{i}") for i in range(3)]
        scores = compute_all_bqs(records)
        assert len(scores) == 3
        assert all(isinstance(s, BQSScore) for s in scores)

    def test_detect_patterns_high_rejection(self):
        records = [
            make_audit_record(entry_id=f"e{i}", verdict=CritiqueVerdict.REJECT, tag=EpistemicTag.REJECTED)
            for i in range(6)
        ] + [
            make_audit_record(entry_id=f"ok{i}", verdict=CritiqueVerdict.PASS)
            for i in range(4)
        ]
        scores = compute_all_bqs(records)
        patterns = detect_patterns(records, scores)
        assert any(p["type"] == "high_rejection_rate" for p in patterns)

    def test_detect_patterns_empty_records(self):
        patterns = detect_patterns([], [])
        assert patterns == []

    def test_source_weight_updates(self):
        records = [
            make_audit_record(entry_id=f"e{i}", source_reference="src/main.py")
            for i in range(3)
        ]
        scores = compute_all_bqs(records)
        updates = compute_source_weight_updates(records, scores)
        assert "src/main.py" in updates
        assert 0.1 <= updates["src/main.py"] <= 0.95


# ---------------------------------------------------------------------------
# Insight Generation Tests
# ---------------------------------------------------------------------------

class TestInsights:

    def test_generate_insights_from_rejection_pattern(self):
        patterns = [{"type": "high_rejection_rate", "severity": "HIGH", "description": "60% rejected", "affected_entries": ["e1", "e2"]}]
        insights = generate_insights_from_patterns(patterns, "s1", "c1")
        assert len(insights) == 1
        assert insights[0].category == "recurring_rejection"
        assert insights[0].severity == "HIGH"

    def test_generate_insights_unknown_pattern(self):
        patterns = [{"type": "unknown_pattern", "severity": "LOW", "description": "test"}]
        insights = generate_insights_from_patterns(patterns, "s1", "c1")
        assert len(insights) == 1
        assert "unknown_pattern" in insights[0].category

    def test_generate_insights_empty(self):
        insights = generate_insights_from_patterns([], "s1", "c1")
        assert insights == []


# ---------------------------------------------------------------------------
# Learning Loop Tests
# ---------------------------------------------------------------------------

class TestLearningLoop:

    def test_full_cycle(self, tmp_db, now):
        logger = AuditLogger(db_path=tmp_db)
        for i in range(5):
            logger.record(make_audit_record(
                entry_id=f"loop-{i}",
                recorded_at=now - timedelta(minutes=30 - i),
            ))

        oracle = OracleLearningLoop(audit_logger=logger)
        report = oracle.analyze(
            session_id="oracle-test",
            range_start=now - timedelta(hours=1),
            range_end=now + timedelta(minutes=5),
            now=now,
        )

        assert isinstance(report, OracleReport)
        assert report.total_entries_analyzed == 5
        assert len(report.bqs_scores) == 5
        assert 0.0 <= report.avg_bqs <= 1.0

    def test_idempotency(self, tmp_db, now):
        logger = AuditLogger(db_path=tmp_db)
        logger.record(make_audit_record(entry_id="idem-1", recorded_at=now))

        oracle = OracleLearningLoop(audit_logger=logger)
        start = now - timedelta(hours=1)
        end = now + timedelta(minutes=5)

        report1 = oracle.analyze("oracle-test", start, end, now=now)
        report2 = oracle.analyze("oracle-test", start, end, now=now)
        assert report1.cycle_id == report2.cycle_id
        assert report1.report_id == report2.report_id

    def test_empty_session(self, tmp_db, now):
        logger = AuditLogger(db_path=tmp_db)
        oracle = OracleLearningLoop(audit_logger=logger)
        report = oracle.analyze(
            session_id="empty-session",
            range_start=now - timedelta(hours=1),
            range_end=now,
        )
        assert report.total_entries_analyzed == 0
        assert report.avg_bqs == 0.0

    def test_source_weights_accumulate(self, tmp_db, now):
        logger = AuditLogger(db_path=tmp_db)
        for i in range(3):
            logger.record(make_audit_record(
                entry_id=f"sw-{i}",
                source_reference="src/main.py",
                recorded_at=now - timedelta(minutes=10 - i),
            ))

        oracle = OracleLearningLoop(audit_logger=logger)
        oracle.analyze(
            session_id="oracle-test",
            range_start=now - timedelta(hours=1),
            range_end=now + timedelta(minutes=5),
            now=now,
        )
        assert "src/main.py" in oracle.source_weights

    def test_get_session_reports(self, tmp_db, now):
        logger = AuditLogger(db_path=tmp_db)
        logger.record(make_audit_record(entry_id="rpt-1", recorded_at=now))

        oracle = OracleLearningLoop(audit_logger=logger)
        oracle.analyze("oracle-test", now - timedelta(hours=1), now + timedelta(minutes=5))

        reports = oracle.get_session_reports("oracle-test")
        assert len(reports) == 1
