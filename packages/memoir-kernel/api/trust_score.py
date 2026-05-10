"""
Trust Score API
-----------------
Composites CPI, average BQS, conflict rate, and attestation count
into a single 0-100 trust score for any agent session.

My thinking here: investors and compliance officers want ONE number.
Not six CPI signals, not five BQS dimensions. One number that says
"how trustworthy is this agent right now?" Think credit score for AI.

The weighting reflects my belief that epistemic quality (BQS) matters
most, followed by behavioral pressure (CPI), then track record
(attestation density and conflict resolution rate).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from memoir.core.audit import AuditLogger
from memoir.models.memory import AuditRecord, CritiqueVerdict, EpistemicTag
from memoir.mirror.cpi import compute_cpi_from_audit
from memoir.oracle.bqs import compute_bqs


class TrustScoreBreakdown(BaseModel):
    """Individual components that feed into the trust score."""
    model_config = ConfigDict(extra="forbid")

    bqs_component: float = Field(ge=0, le=100, description="Belief quality contribution (0-100)")
    cpi_component: float = Field(ge=0, le=100, description="Cognitive pressure contribution (0-100, inverted: low pressure = high score)")
    attestation_component: float = Field(ge=0, le=100, description="On-chain attestation density (0-100)")
    conflict_component: float = Field(ge=0, le=100, description="Conflict resolution rate (0-100)")
    rejection_component: float = Field(ge=0, le=100, description="Low rejection rate = high trust (0-100)")


class TrustScoreResult(BaseModel):
    """The composite trust score for an agent session."""
    model_config = ConfigDict(extra="forbid")

    session_id: str
    agent_id: Optional[str] = None
    trust_score: float = Field(ge=0, le=100, description="Composite trust score 0-100")
    risk_tier: str = Field(description="TRUSTED, CAUTIOUS, ELEVATED, CRITICAL")
    breakdown: TrustScoreBreakdown
    total_beliefs: int
    total_attestations: int
    total_conflicts: int
    conflicts_resolved: int
    avg_bqs: float
    cpi_score: float
    computed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# Weights for each component of the trust score
# I spent time thinking about what matters most:
# BQS is the strongest signal because it directly measures belief quality
# CPI matters because high cognitive pressure predicts failures
# Attestation density shows the agent is being properly governed
# Conflict handling shows the system is catching and resolving issues
WEIGHTS = {
    "bqs": 0.35,
    "cpi": 0.25,
    "attestation": 0.15,
    "conflict": 0.15,
    "rejection": 0.10,
}


def _risk_tier(score: float) -> str:
    """Map trust score to a human-readable risk tier."""
    if score >= 80:
        return "TRUSTED"
    elif score >= 60:
        return "CAUTIOUS"
    elif score >= 40:
        return "ELEVATED"
    else:
        return "CRITICAL"


def compute_trust_score(
    audit_logger: AuditLogger,
    session_id: str,
    agent_id: Optional[str] = None,
) -> TrustScoreResult:
    """
    Compute a composite trust score from all available signals.

    The score runs 0-100 where higher is better (more trustworthy).
    Each component is normalized to 0-100, then weighted.
    """
    # Pull all records for this session
    records = audit_logger.query(session_id=session_id, limit=500)

    if not records:
        return TrustScoreResult(
            session_id=session_id,
            agent_id=agent_id,
            trust_score=50.0,
            risk_tier="CAUTIOUS",
            breakdown=TrustScoreBreakdown(
                bqs_component=50.0,
                cpi_component=50.0,
                attestation_component=0.0,
                conflict_component=100.0,
                rejection_component=100.0,
            ),
            total_beliefs=0,
            total_attestations=0,
            total_conflicts=0,
            conflicts_resolved=0,
            avg_bqs=0.0,
            cpi_score=0.0,
        )

    total_beliefs = len(records)

    # BQS component: average belief quality across the session
    # Higher BQS = higher trust
    bqs_scores = []
    for record in records:
        bqs = compute_bqs(record)
        bqs_scores.append(bqs.composite_score)

    avg_bqs = sum(bqs_scores) / len(bqs_scores) if bqs_scores else 0.5
    bqs_component = avg_bqs * 100

    # CPI component: cognitive pressure is INVERTED
    # Low CPI = high trust (the agent is calm and well-calibrated)
    try:
        cpi = compute_cpi_from_audit(audit_logger, session_id, window_size=min(20, total_beliefs))
        cpi_score = cpi.composite_score
    except Exception:
        cpi_score = 0.0
    # Invert: 0 CPI = 100 trust, 1.0 CPI = 0 trust
    cpi_component = max(0.0, (1.0 - cpi_score)) * 100

    # Attestation component: what percentage of beliefs are attested on-chain
    attested_count = sum(
        1 for r in records
        if (r.metadata or {}).get("chain_tx_signature")
    )
    attestation_ratio = attested_count / total_beliefs if total_beliefs > 0 else 0.0
    attestation_component = attestation_ratio * 100

    # Conflict component: if conflicts exist, how many were resolved
    # No conflicts at all = perfect score (no issues to resolve)
    conflict_records = [
        r for r in records
        if r.critique_verdict == CritiqueVerdict.FLAG
    ]
    total_conflicts = len(conflict_records)
    # For now, flagged records that were later followed by a PASS
    # on the same key count as "resolved"
    resolved_keys = set()
    flagged_keys = set()
    for r in sorted(records, key=lambda x: x.recorded_at):
        if r.critique_verdict == CritiqueVerdict.FLAG:
            flagged_keys.add(r.key)
        elif r.critique_verdict == CritiqueVerdict.PASS and r.key in flagged_keys:
            resolved_keys.add(r.key)

    conflicts_resolved = len(resolved_keys)
    if total_conflicts > 0:
        conflict_component = (conflicts_resolved / total_conflicts) * 100
    else:
        conflict_component = 100.0

    # Rejection component: low rejection rate = high trust
    rejected_count = sum(
        1 for r in records
        if r.critique_verdict == CritiqueVerdict.REJECT
    )
    rejection_rate = rejected_count / total_beliefs if total_beliefs > 0 else 0.0
    # Invert: 0% rejection = 100 trust, 100% rejection = 0 trust
    rejection_component = max(0.0, (1.0 - rejection_rate)) * 100

    # Composite score
    breakdown = TrustScoreBreakdown(
        bqs_component=round(bqs_component, 2),
        cpi_component=round(cpi_component, 2),
        attestation_component=round(attestation_component, 2),
        conflict_component=round(conflict_component, 2),
        rejection_component=round(rejection_component, 2),
    )

    trust_score = (
        WEIGHTS["bqs"] * bqs_component
        + WEIGHTS["cpi"] * cpi_component
        + WEIGHTS["attestation"] * attestation_component
        + WEIGHTS["conflict"] * conflict_component
        + WEIGHTS["rejection"] * rejection_component
    )

    trust_score = round(min(100.0, max(0.0, trust_score)), 2)

    return TrustScoreResult(
        session_id=session_id,
        agent_id=agent_id,
        trust_score=trust_score,
        risk_tier=_risk_tier(trust_score),
        breakdown=breakdown,
        total_beliefs=total_beliefs,
        total_attestations=attested_count,
        total_conflicts=total_conflicts,
        conflicts_resolved=conflicts_resolved,
        avg_bqs=round(avg_bqs, 4),
        cpi_score=round(cpi_score, 4),
    )
