"""
MIRROR/CPI Engine Tests
------------------------
Tests for CPI signal computation, intervention decisions,
reflection prompt generation, and calibration drift detection.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from memoir.core.audit import AuditLogger
from memoir.models.memory import (
    AuditRecord,
    CPIScore,
    CPISignals,
    CritiqueVerdict,
    EpistemicTag,
)
from memoir.mirror.cpi import (
    compute_cpi,
    compute_cpi_from_audit,
    compute_confidence_accuracy_gap,
    compute_contradiction_tolerance,
    compute_decision_velocity,
    compute_epistemic_drift_rate,
    compute_source_citation_drop,
    compute_ttl_violation_rate,
)
from memoir.mirror.intervention import check_intervention, InterventionDecision
from memoir.mirror.reflection import generate_reflection_prompt, ReflectionPrompt
from memoir.mirror.calibration import CalibrationTracker, CalibrationStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test_mirror_audit.db")


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


def make_record(
    tag: EpistemicTag = EpistemicTag.VERIFIED,
    verdict: CritiqueVerdict = CritiqueVerdict.PASS,
    source: str | None = "src/main.py",
    entry_id: str = "e1",
    session_id: str = "mirror-test",
    recorded_at: datetime | None = None,
    critique_notes: str | None = None,
) -> AuditRecord:
    return AuditRecord(
        entry_id=entry_id,
        session_id=session_id,
        key=f"key_{entry_id}",
        operation="WRITE_APPROVED",
        epistemic_tag=tag,
        critique_verdict=verdict,
        source_reference=source,
        critique_notes=critique_notes,
        recorded_at=recorded_at or datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# CPI Signal Tests
# ---------------------------------------------------------------------------

class TestCPISignals:

    def test_epistemic_drift_no_records(self):
        assert compute_epistemic_drift_rate([]) == 0.0

    def test_epistemic_drift_stable(self, now):
        records = [
            make_record(tag=EpistemicTag.VERIFIED, entry_id=f"e{i}", recorded_at=now + timedelta(seconds=i))
            for i in range(8)
        ]
        drift = compute_epistemic_drift_rate(records)
        assert drift == 0.0  # all same tag

    def test_epistemic_drift_unstable(self, now):
        records = [
            make_record(tag=EpistemicTag.VERIFIED, entry_id=f"e{i}", recorded_at=now + timedelta(seconds=i))
            for i in range(4)
        ] + [
            make_record(tag=EpistemicTag.ASSUMED, entry_id=f"e{i+4}", recorded_at=now + timedelta(seconds=i + 4))
            for i in range(4)
        ]
        drift = compute_epistemic_drift_rate(records)
        assert drift > 0.0

    def test_source_citation_drop_no_records(self):
        assert compute_source_citation_drop([]) == 0.0

    def test_source_citation_drop_stable(self, now):
        records = [
            make_record(source="src/main.py", entry_id=f"e{i}", recorded_at=now + timedelta(seconds=i))
            for i in range(8)
        ]
        drop = compute_source_citation_drop(records)
        assert drop == 0.0

    def test_source_citation_drop_declining(self, now):
        records = [
            make_record(source="src/main.py", entry_id=f"e{i}", recorded_at=now + timedelta(seconds=i))
            for i in range(4)
        ] + [
            make_record(source=None, entry_id=f"e{i+4}", recorded_at=now + timedelta(seconds=i + 4))
            for i in range(4)
        ]
        drop = compute_source_citation_drop(records)
        assert drop > 0.0

    def test_contradiction_tolerance_none(self):
        records = [make_record(entry_id=f"e{i}") for i in range(5)]
        tolerance = compute_contradiction_tolerance(records)
        assert tolerance == 0.0

    def test_contradiction_tolerance_flagged(self):
        records = [
            make_record(
                entry_id=f"e{i}",
                verdict=CritiqueVerdict.FLAG,
                critique_notes="Found contradiction with existing belief",
            )
            for i in range(3)
        ]
        tolerance = compute_contradiction_tolerance(records)
        assert tolerance > 0.0

    def test_ttl_violation_rate_none(self):
        records = [make_record(entry_id=f"e{i}") for i in range(5)]
        rate = compute_ttl_violation_rate(records)
        assert rate == 0.0

    def test_ttl_violation_rate_stale(self):
        records = [
            make_record(tag=EpistemicTag.STALE, entry_id=f"e{i}")
            for i in range(3)
        ] + [
            make_record(tag=EpistemicTag.VERIFIED, entry_id=f"ok{i}")
            for i in range(2)
        ]
        rate = compute_ttl_violation_rate(records)
        assert rate == 0.6

    def test_confidence_accuracy_gap_calibrated(self):
        records = [make_record(entry_id=f"e{i}") for i in range(5)]
        gap = compute_confidence_accuracy_gap(records)
        assert gap == 0.0  # VERIFIED + PASS = no gap

    def test_confidence_accuracy_gap_miscalibrated(self):
        records = [
            make_record(
                tag=EpistemicTag.VERIFIED,
                verdict=CritiqueVerdict.REJECT,
                entry_id=f"e{i}",
            )
            for i in range(5)
        ]
        gap = compute_confidence_accuracy_gap(records)
        assert gap > 0.0

    def test_decision_velocity_stable(self, now):
        records = [
            make_record(entry_id=f"e{i}", recorded_at=now + timedelta(minutes=i * 5))
            for i in range(8)
        ]
        velocity = compute_decision_velocity(records)
        assert velocity == 0.0  # constant rate

    def test_decision_velocity_accelerating(self, now):
        # First half: one per 10 minutes. Second half: one per second.
        records = [
            make_record(entry_id=f"e{i}", recorded_at=now + timedelta(minutes=i * 10))
            for i in range(4)
        ] + [
            make_record(entry_id=f"fast{i}", recorded_at=now + timedelta(minutes=40, seconds=i))
            for i in range(4)
        ]
        velocity = compute_decision_velocity(records)
        assert velocity > 0.0


# ---------------------------------------------------------------------------
# Full CPI Tests
# ---------------------------------------------------------------------------

class TestCPIComputation:

    def test_cpi_all_green(self, now):
        records = [
            make_record(
                tag=EpistemicTag.VERIFIED,
                verdict=CritiqueVerdict.PASS,
                source="src/main.py",
                entry_id=f"e{i}",
                recorded_at=now + timedelta(minutes=i),
            )
            for i in range(10)
        ]
        cpi = compute_cpi("mirror-test", records)
        assert cpi.intervention_level == "GREEN"
        assert cpi.composite_score < 0.3

    def test_cpi_model_compute(self):
        signals = CPISignals(
            epistemic_drift_rate=0.0,
            source_citation_drop=0.0,
            contradiction_tolerance=0.0,
            ttl_violation_rate=0.0,
            confidence_accuracy_gap=0.0,
            decision_velocity=0.0,
        )
        cpi = CPIScore.compute("s1", signals, 10)
        assert cpi.composite_score == 0.0
        assert cpi.intervention_level == "GREEN"

    def test_cpi_high_pressure(self):
        signals = CPISignals(
            epistemic_drift_rate=0.9,
            source_citation_drop=0.8,
            contradiction_tolerance=0.7,
            ttl_violation_rate=0.6,
            confidence_accuracy_gap=0.8,
            decision_velocity=0.9,
        )
        cpi = CPIScore.compute("s1", signals, 10)
        assert cpi.composite_score >= 0.7
        assert cpi.intervention_level == "RED"

    def test_cpi_from_audit(self, tmp_db, now):
        logger = AuditLogger(db_path=tmp_db)
        for i in range(5):
            logger.record(make_record(entry_id=f"audit-{i}", recorded_at=now + timedelta(minutes=i)))

        cpi = compute_cpi_from_audit(logger, "mirror-test", window_size=10)
        assert isinstance(cpi, CPIScore)


# ---------------------------------------------------------------------------
# Intervention Tests
# ---------------------------------------------------------------------------

class TestIntervention:

    def test_green_no_action(self):
        signals = CPISignals(
            epistemic_drift_rate=0.0, source_citation_drop=0.0,
            contradiction_tolerance=0.0, ttl_violation_rate=0.0,
            confidence_accuracy_gap=0.0, decision_velocity=0.0,
        )
        cpi = CPIScore.compute("s1", signals, 10)
        decision = check_intervention(cpi, "LOW")
        assert decision.action == "NONE"
        assert not decision.blocks_execution

    def test_red_emergency_stop(self):
        signals = CPISignals(
            epistemic_drift_rate=0.9, source_citation_drop=0.9,
            contradiction_tolerance=0.9, ttl_violation_rate=0.9,
            confidence_accuracy_gap=0.9, decision_velocity=0.9,
        )
        cpi = CPIScore.compute("s1", signals, 10)
        decision = check_intervention(cpi, "LOW")
        assert decision.action == "EMERGENCY_STOP"
        assert decision.blocks_execution

    def test_risk_level_tightens_thresholds(self):
        # A moderate CPI should trigger harder with CRITICAL risk
        signals = CPISignals(
            epistemic_drift_rate=0.3, source_citation_drop=0.3,
            contradiction_tolerance=0.3, ttl_violation_rate=0.3,
            confidence_accuracy_gap=0.3, decision_velocity=0.3,
        )
        cpi = CPIScore.compute("s1", signals, 10)

        low_risk = check_intervention(cpi, "LOW")
        critical_risk = check_intervention(cpi, "CRITICAL")
        # CRITICAL should be same or higher severity
        severity_order = {"NONE": 0, "SOFT_REFLECT": 1, "HARD_REFLECT": 2, "EMERGENCY_STOP": 3}
        assert severity_order[critical_risk.action] >= severity_order[low_risk.action]


# ---------------------------------------------------------------------------
# Reflection Tests
# ---------------------------------------------------------------------------

class TestReflection:

    def test_generate_reflection_prompt(self, now):
        records = [make_record(entry_id=f"r{i}", recorded_at=now) for i in range(5)]
        signals = CPISignals(
            epistemic_drift_rate=0.5, source_citation_drop=0.4,
            contradiction_tolerance=0.3, ttl_violation_rate=0.2,
            confidence_accuracy_gap=0.1, decision_velocity=0.3,
        )
        cpi = CPIScore.compute("mirror-test", signals, 5)
        prompt = generate_reflection_prompt("mirror-test", cpi, records)

        assert isinstance(prompt, ReflectionPrompt)
        assert len(prompt.questions) > 0
        assert len(prompt.recent_beliefs) <= 5

    def test_reflection_prompt_empty_records(self):
        signals = CPISignals(
            epistemic_drift_rate=0.0, source_citation_drop=0.0,
            contradiction_tolerance=0.0, ttl_violation_rate=0.0,
            confidence_accuracy_gap=0.0, decision_velocity=0.0,
        )
        cpi = CPIScore.compute("s1", signals, 0)
        prompt = generate_reflection_prompt("s1", cpi, [])
        assert "No recent beliefs" in prompt.window_summary


# ---------------------------------------------------------------------------
# Calibration Tests
# ---------------------------------------------------------------------------

class TestCalibration:

    def test_healthy_calibration(self, now):
        tracker = CalibrationTracker()
        records = [
            make_record(tag=EpistemicTag.VERIFIED, verdict=CritiqueVerdict.PASS, entry_id=f"c{i}")
            for i in range(5)
        ]
        status = tracker.update("cal-test", records)
        assert not status.is_drifting
        assert status.drift_score < 0.4

    def test_drift_detection_after_consecutive_gaps(self, now):
        tracker = CalibrationTracker()
        # Repeatedly show high gap (VERIFIED getting REJECTED)
        bad_records = [
            make_record(
                tag=EpistemicTag.VERIFIED,
                verdict=CritiqueVerdict.REJECT,
                entry_id=f"bad{i}",
            )
            for i in range(5)
        ]
        for _ in range(4):
            status = tracker.update("drift-test", bad_records)

        assert status.is_drifting
        assert status.consecutive_high_gaps >= 3

    def test_reset_clears_history(self):
        tracker = CalibrationTracker()
        records = [make_record(entry_id=f"r{i}") for i in range(5)]
        tracker.update("reset-test", records)
        tracker.reset("reset-test")
        # After reset, no drift
        status = tracker.update("reset-test", records)
        assert not status.is_drifting
        assert status.consecutive_high_gaps <= 1

    def test_no_records(self):
        tracker = CalibrationTracker()
        status = tracker.update("empty-test", [])
        assert not status.is_drifting
        assert status.drift_score == 0.0
