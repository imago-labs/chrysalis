# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at the root of this repository
# or at http://www.apache.org/licenses/LICENSE-2.0
"""
Belief Conflict Detector
-------------------------
Scans existing beliefs for contradictions when a new belief is about
to be written. I'm using keyword-based heuristics for contradiction
detection rather than embeddings (for now) to keep this fast and
deterministic in tests.

The detector runs between Stage 2 (Verifier) and Stage 3 (Critique)
in the pipeline. Detected conflicts get passed to the critique agent
as additional context.
"""

from __future__ import annotations

import re

from memoir.models.memory import (
    AuditRecord,
    ConflictType,
    EpistemicTag,
)
from memoir.conflicts.models import ConflictCandidate


# Common English stopwords to filter out of Jaccard overlap. Without this,
# beliefs sharing words like "the", "is", "that" register as related when
# they have nothing in common topically.
STOPWORDS: set[str] = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must", "ought",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her",
    "us", "them", "my", "your", "his", "its", "our", "their",
    "this", "that", "these", "those", "what", "which", "who", "whom",
    "and", "but", "or", "nor", "not", "so", "yet", "both", "either",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "as",
    "into", "about", "between", "through", "during", "before", "after",
    "if", "then", "than", "because", "while", "where", "when", "how",
    "all", "each", "every", "some", "any", "no", "other", "such",
    "also", "very", "just", "more", "most", "only", "still", "even",
    "too", "quite", "rather", "really", "already",
    # Common in belief text but carry no topical signal
    "belief", "entry", "proposed", "memory", "agent", "assistant",
    "user", "requested", "information", "based", "using",
}

# Negation patterns that suggest direct contradiction
NEGATION_PATTERNS: list[str] = [
    r"\bnot\b", r"\bnever\b", r"\bwon't\b", r"\bcannot\b",
    r"\bfail\b", r"\bcrash\b", r"\bdrop\b", r"\bfall\b",
    r"\bdecline\b", r"\breject\b", r"\bovervalued\b",
    r"\bworthless\b", r"\bfalse\b", r"\bwrong\b",
    r"\bdon't\b", r"\bdoesn't\b", r"\bno longer\b",
]

# Patterns suggesting strong positive claims
POSITIVE_PATTERNS: list[str] = [
    r"\bwill hit\b", r"\bwill reach\b", r"\bwill rise\b",
    r"\bconfirm\b", r"\bapprove\b", r"\bsucceed\b",
    r"\bincrease\b", r"\bgrow\b", r"\bundervalued\b",
    r"\bvaluable\b", r"\btrue\b", r"\bcorrect\b",
    r"\brequires?\b", r"\balways\b",
]


def _extract_key_topic(content: str) -> str:
    """Pull out the main topic/subject for overlap detection."""
    # Simple approach: take the first noun-like phrase
    # In practice this would use NLP, but keywords work for demo
    return content.lower().strip()[:100]


def _content_overlap(a: str, b: str) -> float:
    """
    Word overlap score between two beliefs using Jaccard similarity.
    Filters stopwords so common English filler doesn't create false matches.
    """
    words_a = set(re.findall(r"\w+", a.lower())) - STOPWORDS
    words_b = set(re.findall(r"\w+", b.lower())) - STOPWORDS

    if not words_a or not words_b:
        return 0.0

    intersection = words_a & words_b
    union = words_a | words_b

    return len(intersection) / len(union) if union else 0.0


def _has_negation_pattern(content: str) -> bool:
    """Check if content contains negation language."""
    content_lower = content.lower()
    return any(re.search(p, content_lower) for p in NEGATION_PATTERNS)


def _has_positive_pattern(content: str) -> bool:
    """Check if content contains strong positive claims."""
    content_lower = content.lower()
    return any(re.search(p, content_lower) for p in POSITIVE_PATTERNS)


def detect_conflicts(
    new_entry_id: str,
    new_key: str,
    new_content: str,
    new_tag: str,
    existing_records: list[AuditRecord],
    similarity_threshold: float = 0.30,
) -> list[ConflictCandidate]:
    """
    Scan existing beliefs for potential conflicts with a new belief.

    I'm checking for:
    1. Direct contradiction (opposing sentiment on overlapping topic)
    2. Temporal conflict (new data supersedes stale belief)
    3. Source disagreement (same topic, different values from different sources)
    4. Rule violation (new belief contradicts USER_STATED rules)

    Returns a list of conflict candidates for the resolver to handle.
    """
    candidates: list[ConflictCandidate] = []

    for record in existing_records:
        # Compare new content against the record's content (stored in key)
        # and the critique notes for richer signal
        existing_content = record.content or record.key
        existing_notes = record.critique_notes or ""
        compare_text = existing_content + " " + existing_notes

        # Skip records about completely different topics
        overlap = _content_overlap(new_content, compare_text)
        # Also check key similarity
        key_match = new_key.lower() == record.key.lower()

        if overlap < similarity_threshold and not key_match:
            continue

        # Check for direct contradiction: opposing sentiment on overlapping topic
        new_negative = _has_negation_pattern(new_content)
        new_positive = _has_positive_pattern(new_content)
        existing_negative = _has_negation_pattern(existing_content) or _has_negation_pattern(existing_notes)
        existing_positive = _has_positive_pattern(existing_content) or _has_positive_pattern(existing_notes)

        # Only flag contradiction when one side is clearly positive and
        # the other clearly negative on the same topic (overlap >= threshold)
        if (new_negative and existing_positive) or (new_positive and existing_negative):
            candidates.append(ConflictCandidate(
                belief_a_id=new_entry_id,
                belief_a_key=new_key,
                belief_a_content=new_content,
                belief_a_tag=new_tag,
                belief_b_id=record.entry_id,
                belief_b_key=record.key,
                belief_b_content=existing_content,
                belief_b_tag=record.epistemic_tag.value,
                conflict_type=ConflictType.DIRECT_CONTRADICTION,
                similarity_score=overlap,
                description=f"Contradiction detected: '{new_key}' vs existing '{record.key}' (overlap: {overlap:.2f})",
            ))
            continue

        # Rule violation: new belief contradicts a USER_STATED constraint
        if record.epistemic_tag == EpistemicTag.USER_STATED and new_tag != "USER_STATED":
            if overlap > 0.35 or key_match:
                candidates.append(ConflictCandidate(
                    belief_a_id=new_entry_id,
                    belief_a_key=new_key,
                    belief_a_content=new_content,
                    belief_a_tag=new_tag,
                    belief_b_id=record.entry_id,
                    belief_b_key=record.key,
                    belief_b_content=existing_content,
                    belief_b_tag=record.epistemic_tag.value,
                    conflict_type=ConflictType.RULE_VIOLATION,
                    similarity_score=overlap,
                    description=f"New {new_tag} belief may conflict with USER_STATED rule on '{record.key}'",
                ))
                continue

        # Source disagreement: same topic, different epistemic backing
        if record.epistemic_tag == EpistemicTag.VERIFIED and new_tag == "INFERRED":
            if key_match and overlap > 0.35:
                candidates.append(ConflictCandidate(
                    belief_a_id=new_entry_id,
                    belief_a_key=new_key,
                    belief_a_content=new_content,
                    belief_a_tag=new_tag,
                    belief_b_id=record.entry_id,
                    belief_b_key=record.key,
                    belief_b_content=existing_content,
                    belief_b_tag=record.epistemic_tag.value,
                    conflict_type=ConflictType.SOURCE_DISAGREEMENT,
                    similarity_score=overlap,
                    description=f"New INFERRED belief conflicts with VERIFIED belief on '{record.key}'",
                ))
                continue

        # Temporal conflict: stale data being superseded
        if record.epistemic_tag == EpistemicTag.STALE and key_match:
            candidates.append(ConflictCandidate(
                belief_a_id=new_entry_id,
                belief_a_key=new_key,
                belief_a_content=new_content,
                belief_a_tag=new_tag,
                belief_b_id=record.entry_id,
                belief_b_key=record.key,
                belief_b_content=existing_content,
                belief_b_tag=record.epistemic_tag.value,
                conflict_type=ConflictType.TEMPORAL_CONFLICT,
                similarity_score=overlap,
                description=f"New belief supersedes STALE belief on '{record.key}'",
            ))

    return candidates


def detect_rule_violations(
    new_content: str,
    new_tag: str,
    user_stated_records: list[AuditRecord],
) -> list[ConflictCandidate]:
    """
    Specifically check if a new belief violates any USER_STATED rules.
    These are beliefs the user has explicitly stated and should take
    priority unless overridden by VERIFIED market data at CRITICAL risk.
    """
    candidates: list[ConflictCandidate] = []

    for record in user_stated_records:
        notes = record.critique_notes or ""
        overlap = _content_overlap(new_content, notes)
        if overlap < 0.15:
            continue

        # Any new non-USER_STATED belief that overlaps with a rule is suspect
        candidates.append(ConflictCandidate(
            belief_a_id="pending",
            belief_a_key="pending",
            belief_a_content=new_content,
            belief_a_tag=new_tag,
            belief_b_id=record.entry_id,
            belief_b_key=record.key,
            belief_b_content=notes,
            belief_b_tag=record.epistemic_tag.value,
            conflict_type=ConflictType.RULE_VIOLATION,
            similarity_score=overlap,
            description=f"New belief may violate USER_STATED rule on '{record.key}'",
        ))

    return candidates
