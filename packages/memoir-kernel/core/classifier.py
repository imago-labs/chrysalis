# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at the root of this repository
# or at http://www.apache.org/licenses/LICENSE-2.0
"""
MEMOIR Epistemic Classifier
---------------------------
Stage 1 of the validation pipeline.

Classifies a proposed memory entry as VERIFIED, INFERRED, ASSUMED, or STALE
before the critique agent runs. Fast heuristic pass — no LLM call required.
The classifier looks at source reference quality, entry age signals,
and key characteristics to assign an initial epistemic tag.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from memoir.models.memory import EpistemicTag, MemoryEntry


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# How long a VERIFIED entry stays fresh before becoming STALE
VERIFIED_TTL_HOURS = 24

# Patterns that suggest a concrete source reference
SOURCE_PATTERNS = [
    r"^[\w./\-]+\.(py|ts|js|md|json|yaml|yml|txt|csv|toml)$",  # file path
    r"^https?://",                                                 # URL
    r"^#L\d+",                                                    # line ref
    r"^\w+/\w+",                                                   # repo path
]

# Keywords in content that signal inference vs. direct observation
INFERENCE_SIGNALS = [
    "therefore", "which means", "suggests that", "implies",
    "based on", "derived from", "it follows", "consequently",
    "probably", "likely", "appears to", "seems to",
]

ASSUMPTION_SIGNALS = [
    "assume", "presumably", "should be", "ought to",
    "expected to", "by default", "typically", "usually",
    "might be", "could be", "perhaps",
]


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class EpistemicClassifier:
    """
    Heuristic epistemic classifier.

    Assigns an initial EpistemicTag to a MemoryEntry based on:
    - Presence and quality of a source reference
    - Content language signals (inference vs. assumption)
    - Entry age relative to TTL thresholds

    This runs before the LLM critique pass to give the critique agent
    a prior to work with.
    """

    def classify(
        self,
        entry: MemoryEntry,
        existing_verified_at: Optional[datetime] = None,
    ) -> EpistemicTag:
        """
        Classify a MemoryEntry and return its EpistemicTag.

        Args:
            entry: The proposed memory write.
            existing_verified_at: If this key already exists in the store
                                   and was previously VERIFIED, pass that
                                   timestamp to check for staleness.

        Returns:
            EpistemicTag assigned to this entry.
        """
        # Check staleness first -- if a prior VERIFIED entry has aged out,
        # we reclassify before any other logic runs.
        if existing_verified_at is not None:
            age = datetime.now(timezone.utc) - existing_verified_at
            if age > timedelta(hours=VERIFIED_TTL_HOURS):
                return EpistemicTag.STALE

        # Strong source reference -> candidate for VERIFIED
        if self._has_strong_source(entry.source_reference):
            return EpistemicTag.VERIFIED

        # If the agent chat layer already classified this belief via LLM
        # extraction, respect that tag instead of overriding with heuristics.
        # The context string carries the extracted tag from the chat endpoint.
        extracted = self._get_extracted_tag(entry.human_session_context)
        if extracted:
            return extracted

        # Assumption language signals -> ASSUMED
        if self._has_assumption_signals(entry.content):
            return EpistemicTag.ASSUMED

        # Inference language signals -> INFERRED
        if self._has_inference_signals(entry.content):
            return EpistemicTag.INFERRED

        # No source reference at all -> ASSUMED
        if not entry.source_reference:
            return EpistemicTag.ASSUMED

        # Weak source reference but no inference/assumption signals -> INFERRED
        return EpistemicTag.INFERRED

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _get_extracted_tag(self, context: Optional[str]) -> Optional[EpistemicTag]:
        """Pull the LLM-extracted epistemic tag from the human_session_context field.

        The agent chat endpoint writes 'Extracted from chat, tag: VERIFIED'
        (or USER_STATED, AGENT_DERIVED, etc.) into the context. If present,
        return the matching EpistemicTag so the classifier respects the
        LLM's judgment over simple heuristics.
        """
        if not context or "tag:" not in context:
            return None
        tag_map = {
            "VERIFIED": EpistemicTag.VERIFIED,
            "USER_STATED": EpistemicTag.USER_STATED,
            "AGENT_DERIVED": EpistemicTag.AGENT_DERIVED,
            "ASSUMED": EpistemicTag.ASSUMED,
            "INFERRED": EpistemicTag.INFERRED,
        }
        for label, tag in tag_map.items():
            if f"tag: {label}" in context:
                return tag
        return None

    def _has_strong_source(self, source_reference: Optional[str]) -> bool:
        """Return True if the source reference looks concrete and verifiable."""
        if not source_reference:
            return False
        for pattern in SOURCE_PATTERNS:
            if re.match(pattern, source_reference.strip()):
                return True
        return False

    def _has_inference_signals(self, content: str) -> bool:
        """Return True if content language suggests derivation from other facts."""
        content_lower = content.lower()
        return any(signal in content_lower for signal in INFERENCE_SIGNALS)

    def _has_assumption_signals(self, content: str) -> bool:
        """Return True if content language suggests the agent is guessing."""
        content_lower = content.lower()
        return any(signal in content_lower for signal in ASSUMPTION_SIGNALS)

    def compute_stale_after(self, tag: EpistemicTag) -> Optional[datetime]:
        """
        Compute the datetime after which this entry should be reclassified.

        Only VERIFIED entries have a TTL. INFERRED and ASSUMED entries are
        considered indefinitely uncertain — the drift detector handles them.
        """
        if tag == EpistemicTag.VERIFIED:
            return datetime.now(timezone.utc) + timedelta(hours=VERIFIED_TTL_HOURS)
        return None
