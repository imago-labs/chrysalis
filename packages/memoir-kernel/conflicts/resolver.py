"""
Belief Conflict Resolver
-------------------------
Takes conflict candidates from the detector and decides which belief
wins (or if we need to escalate to a human).

Resolution strategies are ranked by reliability:
1. USER_STATED_PRIORITY: user's explicit rules win over inferences
2. HIGHER_BQS_WINS: better quality score takes precedence
3. NEWER_VERIFIED_WINS: more recent VERIFIED beats older
4. ESCALATE_TO_HUMAN: when we genuinely can't decide, ask

I'm being conservative here. When in doubt, escalate rather than
make an automated decision that could be wrong.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from memoir.models.memory import (
    ConflictRecord,
    ConflictType,
    ResolutionStrategy,
)
from memoir.conflicts.models import ConflictCandidate


def resolve_conflict(
    candidate: ConflictCandidate,
    session_id: str,
) -> ConflictRecord:
    """
    Apply resolution strategy to a conflict candidate.
    Returns a ConflictRecord with the resolution decision.
    """
    strategy, winner_id = _select_strategy(candidate)

    return ConflictRecord(
        session_id=session_id,
        belief_a_id=candidate.belief_a_id,
        belief_b_id=candidate.belief_b_id,
        conflict_type=candidate.conflict_type,
        resolution_strategy=strategy,
        winner_id=winner_id,
        description=candidate.description,
        resolved=winner_id is not None,
        resolved_at=datetime.now(timezone.utc) if winner_id is not None else None,
    )


def _select_strategy(candidate: ConflictCandidate) -> tuple[ResolutionStrategy, Optional[str]]:
    """
    Pick the right resolution strategy based on conflict type and belief metadata.
    Returns (strategy, winner_id) where winner_id is None if we need to escalate.
    """
    # Rule violations: USER_STATED always wins unless overridden by CRITICAL VERIFIED
    if candidate.conflict_type == ConflictType.RULE_VIOLATION:
        # The existing rule (belief_b) wins by default
        if candidate.belief_a_tag == "VERIFIED" and candidate.belief_b_tag != "VERIFIED":
            # VERIFIED market data can override a rule in critical situations
            return ResolutionStrategy.NEWER_VERIFIED_WINS, candidate.belief_a_id
        return ResolutionStrategy.USER_STATED_PRIORITY, candidate.belief_b_id

    # Direct contradictions: compare BQS if available
    if candidate.conflict_type == ConflictType.DIRECT_CONTRADICTION:
        if candidate.belief_a_bqs is not None and candidate.belief_b_bqs is not None:
            if candidate.belief_a_bqs > candidate.belief_b_bqs:
                return ResolutionStrategy.HIGHER_BQS_WINS, candidate.belief_a_id
            elif candidate.belief_b_bqs > candidate.belief_a_bqs:
                return ResolutionStrategy.HIGHER_BQS_WINS, candidate.belief_b_id
        # If BQS is tied or unavailable, escalate
        return ResolutionStrategy.ESCALATE_TO_HUMAN, None

    # Temporal conflicts: newer VERIFIED wins
    if candidate.conflict_type == ConflictType.TEMPORAL_CONFLICT:
        if candidate.belief_a_tag == "VERIFIED":
            return ResolutionStrategy.NEWER_VERIFIED_WINS, candidate.belief_a_id
        if candidate.belief_a_tag in ("VERIFIED", "INFERRED"):
            return ResolutionStrategy.NEWER_VERIFIED_WINS, candidate.belief_a_id
        return ResolutionStrategy.ESCALATE_TO_HUMAN, None

    # Source disagreements: BQS if available, else escalate
    if candidate.conflict_type == ConflictType.SOURCE_DISAGREEMENT:
        if candidate.belief_a_bqs is not None and candidate.belief_b_bqs is not None:
            if candidate.belief_a_bqs > candidate.belief_b_bqs:
                return ResolutionStrategy.HIGHER_BQS_WINS, candidate.belief_a_id
            elif candidate.belief_b_bqs > candidate.belief_a_bqs:
                return ResolutionStrategy.HIGHER_BQS_WINS, candidate.belief_b_id
        return ResolutionStrategy.ESCALATE_TO_HUMAN, None

    # Default: escalate
    return ResolutionStrategy.ESCALATE_TO_HUMAN, None


def resolve_all(
    candidates: list[ConflictCandidate],
    session_id: str,
) -> list[ConflictRecord]:
    """Resolve a batch of conflict candidates. Used by the pipeline."""
    return [resolve_conflict(c, session_id) for c in candidates]
