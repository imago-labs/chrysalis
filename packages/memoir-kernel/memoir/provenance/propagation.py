# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at the root of this repository
# or at http://www.apache.org/licenses/LICENSE-2.0
"""
Quality Signal Propagation
---------------------------
When a parent belief gets rejected by ORACLE, all downstream derivatives
need to be flagged for re-evaluation. This module handles that cascading
notification.

I'm using an event-based model where rejection produces a list of
affected entries. The caller is responsible for actually re-evaluating
them (ORACLE or the pipeline). This keeps propagation fast and
side-effect-free.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from memoir.provenance.graph import DerivationGraph


class PropagationEvent(BaseModel):
    """Notification that a derived belief needs re-evaluation."""
    model_config = ConfigDict(extra="forbid")

    parent_attestation_id: str
    child_attestation_id: str
    child_entry_id: str
    child_agent_id: str
    reason: str
    original_bqs: float
    discounted_bqs: float
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PropagationResult(BaseModel):
    """Result of propagating a rejection through the derivation graph."""
    model_config = ConfigDict(extra="forbid")

    rejected_attestation_id: str
    total_affected: int
    events: list[PropagationEvent]
    propagated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def propagate_rejection(
    graph: DerivationGraph,
    rejected_attestation_id: str,
    reason: str = "Parent belief rejected by ORACLE",
) -> PropagationResult:
    """
    Propagate a belief rejection through the derivation graph.
    Finds all downstream derivatives and creates re-evaluation events for each.
    """
    descendants = graph.get_all_descendants(rejected_attestation_id)

    events: list[PropagationEvent] = []
    for edge in descendants:
        events.append(PropagationEvent(
            parent_attestation_id=edge.parent_attestation_id,
            child_attestation_id=edge.child_attestation_id,
            child_entry_id=edge.child_entry_id,
            child_agent_id=edge.child_agent_id,
            reason=reason,
            original_bqs=edge.original_bqs,
            discounted_bqs=edge.discounted_bqs,
        ))

    return PropagationResult(
        rejected_attestation_id=rejected_attestation_id,
        total_affected=len(events),
        events=events,
    )
