# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at the root of this repository
# or at http://www.apache.org/licenses/LICENSE-2.0
"""
AGENT_DERIVED Belief Type Handling
------------------------------------
When Agent B receives a belief from Agent A, we need to track that
provenance chain. The key insight is that trust degrades with each
handoff. I'm applying a 0.85x BQS discount per hop because second-hand
information is inherently less reliable than first-hand observation.

The discount is multiplicative: after 2 hops, BQS is 0.85 * 0.85 = 0.7225.
This means beliefs naturally degrade to worthlessness after enough handoffs,
which is the right behavior.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from memoir.models.memory import BQSScore, EpistemicTag


# Each handoff between agents reduces BQS by this factor
HANDOFF_DISCOUNT: float = 0.85


class AgentDerivedMetadata(BaseModel):
    """Provenance metadata for beliefs inherited from other agents."""
    model_config = ConfigDict(extra="forbid")

    source_agent_id: str
    original_attestation_id: Optional[str] = None
    original_epistemic_tag: str
    original_bqs: float
    handoff_count: int = Field(ge=1, default=1)
    discounted_bqs: float
    derivation_chain: list[str] = Field(
        default_factory=list,
        description="List of agent IDs in the derivation chain, oldest first",
    )
    derived_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def compute_discounted_bqs(original_bqs: float, handoff_count: int = 1) -> float:
    """
    Apply the handoff discount to a BQS score.
    Each hop multiplies by HANDOFF_DISCOUNT.
    """
    discounted = original_bqs * (HANDOFF_DISCOUNT ** handoff_count)
    return round(max(0.0, min(1.0, discounted)), 4)


def create_agent_derived_metadata(
    source_agent_id: str,
    original_attestation_id: Optional[str],
    original_epistemic_tag: str,
    original_bqs: float,
    existing_chain: Optional[list[str]] = None,
) -> AgentDerivedMetadata:
    """
    Build metadata for a belief being passed from one agent to another.
    Tracks the full derivation chain for auditability.
    """
    chain = list(existing_chain or [])
    chain.append(source_agent_id)
    handoff_count = len(chain)

    return AgentDerivedMetadata(
        source_agent_id=source_agent_id,
        original_attestation_id=original_attestation_id,
        original_epistemic_tag=original_epistemic_tag,
        original_bqs=original_bqs,
        handoff_count=handoff_count,
        discounted_bqs=compute_discounted_bqs(original_bqs, handoff_count),
        derivation_chain=chain,
    )
