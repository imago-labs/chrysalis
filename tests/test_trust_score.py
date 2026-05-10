# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at the root of this repository
# or at http://www.apache.org/licenses/LICENSE-2.0
"""
Trust Score Tests
-------------------
Making sure the composite trust score computes correctly across
different session states. Testing the edge cases: empty session,
all rejected, all approved, mixed scenarios.
"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from memoir.core.audit import AuditLogger
from memoir.models.memory import (
    AuditRecord,
    CritiqueVerdict,
    EpistemicTag,
)
from memoir.api.trust_score import compute_trust_score, _risk_tier


# ── Risk tier mapping ─────────────────────────────────────────────────

def test_risk_tier_trusted():
    assert _risk_tier(85.0) == "TRUSTED"

def test_risk_tier_cautious():
    assert _risk_tier(65.0) == "CAUTIOUS"

def test_risk_tier_elevated():
    assert _risk_tier(45.0) == "ELEVATED"

def test_risk_tier_critical():
    assert _risk_tier(20.0) == "CRITICAL"

def test_risk_tier_boundary_80():
    assert _risk_tier(80.0) == "TRUSTED"

def test_risk_tier_boundary_60():
    assert _risk_tier(60.0) == "CAUTIOUS"

def test_risk_tier_boundary_40():
    assert _risk_tier(40.0) == "ELEVATED"


# ── Empty session baseline ────────────────────────────────────────────

def test_empty_session_returns_cautious():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test_trust.db")
        audit = AuditLogger(db_path=db_path)
        result = compute_trust_score(audit, "empty-session")
        assert result.trust_score == 50.0
        assert result.risk_tier == "CAUTIOUS"
        assert result.total_beliefs == 0


# ── Session with all approved beliefs ─────────────────────────────────

def test_all_approved_high_trust():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test_trust.db")
        audit = AuditLogger(db_path=db_path)

        for i in range(5):
            record = AuditRecord(
                entry_id=f"entry-{i}",
                session_id="good-session",
                key=f"belief_{i}",
                operation="WRITE_APPROVED",
                epistemic_tag=EpistemicTag.VERIFIED,
                critique_verdict=CritiqueVerdict.PASS,
                critique_notes="Looks good",
                source_reference=f"https://source.com/{i}",
                recorded_at=datetime.now(timezone.utc),
            )
            audit.record(record)

        result = compute_trust_score(audit, "good-session")
        assert result.trust_score > 60.0
        assert result.risk_tier in ("TRUSTED", "CAUTIOUS")
        assert result.total_beliefs == 5
        assert result.breakdown.rejection_component == 100.0


# ── Session with rejected beliefs ─────────────────────────────────────

def test_rejected_beliefs_lower_trust():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test_trust.db")
        audit = AuditLogger(db_path=db_path)

        for i in range(3):
            record = AuditRecord(
                entry_id=f"good-{i}",
                session_id="mixed-session",
                key=f"good_belief_{i}",
                operation="WRITE_APPROVED",
                epistemic_tag=EpistemicTag.VERIFIED,
                critique_verdict=CritiqueVerdict.PASS,
                critique_notes="Good",
                source_reference=f"https://source.com/{i}",
                recorded_at=datetime.now(timezone.utc),
            )
            audit.record(record)

        for i in range(3):
            record = AuditRecord(
                entry_id=f"bad-{i}",
                session_id="mixed-session",
                key=f"bad_belief_{i}",
                operation="WRITE_REJECTED",
                epistemic_tag=EpistemicTag.ASSUMED,
                critique_verdict=CritiqueVerdict.REJECT,
                critique_notes="Unverifiable",
                recorded_at=datetime.now(timezone.utc),
            )
            audit.record(record)

        result = compute_trust_score(audit, "mixed-session")
        assert result.total_beliefs == 6
        assert result.breakdown.rejection_component == pytest.approx(50.0, abs=1.0)


# ── Trust score components are bounded ────────────────────────────────

def test_trust_score_bounded():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test_trust.db")
        audit = AuditLogger(db_path=db_path)

        record = AuditRecord(
            entry_id="single-entry",
            session_id="single-session",
            key="test_key",
            operation="WRITE_APPROVED",
            epistemic_tag=EpistemicTag.ASSUMED,
            critique_verdict=CritiqueVerdict.PASS,
            critique_notes="OK",
            recorded_at=datetime.now(timezone.utc),
        )
        audit.record(record)

        result = compute_trust_score(audit, "single-session")
        assert 0.0 <= result.trust_score <= 100.0
        assert 0.0 <= result.breakdown.bqs_component <= 100.0
        assert 0.0 <= result.breakdown.cpi_component <= 100.0
        assert 0.0 <= result.breakdown.attestation_component <= 100.0
        assert 0.0 <= result.breakdown.conflict_component <= 100.0
        assert 0.0 <= result.breakdown.rejection_component <= 100.0


# ── Agent ID is preserved ─────────────────────────────────────────────

def test_agent_id_passed_through():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test_trust.db")
        audit = AuditLogger(db_path=db_path)
        result = compute_trust_score(audit, "test-session", agent_id="cryptosage-v1")
        assert result.agent_id == "cryptosage-v1"
