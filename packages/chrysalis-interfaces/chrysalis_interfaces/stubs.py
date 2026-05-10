# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at the root of this repository
# or at http://www.apache.org/licenses/LICENSE-2.0
"""
Stub implementations of the four Chrysalis interface protocols.

These stubs are sufficient to run the open kernel end to end on a single
machine with no external dependencies. They are intentionally minimal.
Production deployments substitute real implementations from the
Chrysalis platform.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from chrysalis_interfaces.protocols import (
    AffectMonitor,
    AffectScore,
    AttestationReceipt,
    Attester,
    Belief,
    Chain,
    CoherenceMonitor,
    CoherenceScore,
    Critic,
    CritiqueResult,
    Turn,
)

LOG = logging.getLogger(__name__)


class RuleBasedCritic(Critic):
    """
    Heuristic critic. Flags beliefs that are too short, too long, or lack
    source context. Returns a deterministic quality score in [0, 1].

    For real LLM-driven critique, use the Oracle module of the Chrysalis
    platform or implement your own Critic.
    """

    MIN_LEN: Final[int] = 10
    MAX_LEN: Final[int] = 4_000

    def critique(self, belief: Belief) -> CritiqueResult:
        issues: list[str] = []
        score = 1.0

        if len(belief.content) < self.MIN_LEN:
            issues.append("content_too_short")
            score -= 0.4
        if len(belief.content) > self.MAX_LEN:
            issues.append("content_too_long")
            score -= 0.2
        if not belief.source_context.strip():
            issues.append("missing_source_context")
            score -= 0.3

        score = max(0.0, min(1.0, score))
        return CritiqueResult(
            belief_quality_score=score,
            issues_found=issues,
            confidence=0.5,  # heuristic, not a real estimate
            reasoning="rule_based_stub",
        )


class NoOpCoherenceMonitor(CoherenceMonitor):
    """
    Returns neutral coherence scores. Use the Mirror module of the
    Chrysalis platform or implement your own monitor for real signal.
    """

    def score(self, turn: Turn) -> CoherenceScore:
        return CoherenceScore(
            cpi=0.0,
            lci=0.0,
            rld=0.0,
            scs=0.0,
            her=0.0,
            flags=["stub_no_signal"],
        )


class NoOpAffectMonitor(AffectMonitor):
    """
    Returns neutral affect scores. Use the Resonance module of the
    Chrysalis platform or implement your own monitor for real signal.
    """

    def score(self, turn: Turn) -> AffectScore:
        return AffectScore(
            mcpl=0.0,
            rcs=0.0,
            user_sentiment_delta=0.0,
            agent_behavior_delta=0.0,
            flags=["stub_no_signal"],
        )


class LocalLogAttester(Attester):
    """
    Writes attestations to a local JSONL file. Suitable for development
    and unit tests, not for production. Use the Shield module of the
    Chrysalis platform for real cross-chain anchoring.
    """

    def __init__(self, log_path: str | Path = "./attestations.jsonl") -> None:
        self.log_path = Path(log_path)

    def attest(self, payload: bytes, chain: Chain) -> AttestationReceipt:
        if chain is not Chain.LOCAL:
            LOG.warning(
                "LocalLogAttester ignoring chain=%s and writing to local log. "
                "Use the Shield module for real on-chain attestation.",
                chain,
            )

        payload_hash = hashlib.sha256(payload).hexdigest()
        record = {
            "chain": Chain.LOCAL.value,
            "payload_hash": payload_hash,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

        return AttestationReceipt(
            chain=Chain.LOCAL,
            transaction_id=payload_hash,
            payload_hash=payload_hash,
            timestamp=datetime.now(timezone.utc),
            verifier_url=None,
        )
