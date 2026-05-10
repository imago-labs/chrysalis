# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at the root of this repository
# or at http://www.apache.org/licenses/LICENSE-2.0
"""
MEMOIR Validation Pipeline
--------------------------
Orchestrates the four-stage validation pipeline:

  Stage 1: EpistemicClassifier  — heuristic confidence tagging
  Stage 2: GroundTruthVerifier  — source material retrieval
  Stage 3: CritiqueAgent        — LLM-powered contradiction check
  Stage 4: AuditLogger          — immutable write to audit log

Returns a ValidationResult to the caller (MCP server or REST API).
If the critique verdict is REJECT, the write is blocked.
PASS and FLAG both write to the memory store (FLAG with warning attached).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from memoir.core.audit import AuditLogger
from memoir.core.classifier import EpistemicClassifier
from memoir.core.critique import CritiqueAgent
from memoir.core.verifier import GroundTruthVerifier
from memoir.models.memory import (
    AuditRecord,
    CritiqueVerdict,
    EpistemicTag,
    MemoryEntry,
    ValidationResult,
)


class MEMOIRPipeline:
    """
    The MEMOIR validation pipeline.

    Instantiate once per server/process. All components are stateless
    except the AuditLogger (which holds a DB connection pool).
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        workspace_root: Optional[str] = None,
        anthropic_api_key: Optional[str] = None,
    ):
        self._classifier = EpistemicClassifier()
        self._verifier = GroundTruthVerifier(workspace_root=workspace_root)
        self._critique = CritiqueAgent(api_key=anthropic_api_key)
        self._audit = AuditLogger(db_path=db_path)

    def validate(
        self,
        entry: MemoryEntry,
        existing_verified_at: Optional[datetime] = None,
        existing_memory_content: Optional[str] = None,
    ) -> ValidationResult:
        """
        Run the full four-stage validation pipeline on a proposed MemoryEntry.

        Args:
            entry: The proposed memory write from the agent.
            existing_verified_at: If this key was previously VERIFIED,
                                   pass that timestamp to check staleness.
            existing_memory_content: Current memory content for this key,
                                      for the critique agent to compare against.

        Returns:
            ValidationResult indicating approval status and full audit chain.
        """
        # ── Stage 1: Epistemic Classification ──────────────────────────────
        tag = self._classifier.classify(
            entry, existing_verified_at=existing_verified_at
        )
        stale_after = self._classifier.compute_stale_after(tag)

        # Enrich the entry with classification results
        entry.epistemic_tag = tag
        if stale_after:
            entry.stale_after = stale_after

        # -- Stage 2: Source Material Retrieval --------------------------------
        # For VERIFIED beliefs, try to fetch the actual source material
        # so the critique agent has ground truth to compare against.
        # Tool-backed internal URLs return pre-verified context without
        # needing a real HTTP fetch.
        source_context: Optional[str] = None
        if tag == EpistemicTag.VERIFIED and entry.source_reference:
            source_context, fetch_error = self._verifier.fetch_source_context(entry)
            if fetch_error:
                # Only downgrade if this isn't a tool-verified source.
                # Tool results are pre-verified by execution; a fetch
                # error just means we can't re-read the source file, not
                # that the data is unverified.
                ref = entry.source_reference or ""
                if "tool-result.chrysalis.internal" not in ref:
                    tag = EpistemicTag.INFERRED
                    entry.epistemic_tag = tag

        # ── Stage 3: Critique Agent ─────────────────────────────────────────
        verdict, critique_notes, latency_ms = self._critique.critique(
            entry=entry,
            epistemic_tag=tag,
            source_context=source_context,
            existing_memories=existing_memory_content,
        )

        entry.critique_verdict = verdict
        entry.critique_notes = critique_notes

        if verdict == CritiqueVerdict.PASS:
            operation = "WRITE_APPROVED"
        elif verdict == CritiqueVerdict.FLAG:
            operation = "WRITE_FLAGGED"
        else:
            operation = "WRITE_REJECTED"
            tag = EpistemicTag.REJECTED
            entry.epistemic_tag = tag

        # Mark verification timestamp if VERIFIED and approved
        if tag == EpistemicTag.VERIFIED and verdict != CritiqueVerdict.REJECT:
            entry.verified_at = datetime.now(timezone.utc)

        # ── Stage 4: Audit Log ──────────────────────────────────────────────
        audit_id = str(uuid.uuid4())
        audit_record = AuditRecord(
            audit_id=audit_id,
            entry_id=entry.entry_id,
            session_id=entry.session_id,
            key=entry.key,
            content=entry.content,
            operation=operation,
            epistemic_tag=tag,
            critique_verdict=verdict,
            critique_notes=critique_notes,
            source_reference=entry.source_reference,
            tool_call_id=entry.tool_call_id,
            human_session_context=entry.human_session_context,
            metadata={
                "latency_ms": round(latency_ms, 2),
                "had_source_context": source_context is not None,
                "had_existing_memory": existing_memory_content is not None,
                "stale_after": stale_after.isoformat() if stale_after else None,
            },
        )
        self._audit.record(audit_record)

        # ── Return ValidationResult ─────────────────────────────────────────
        approved = verdict in (CritiqueVerdict.PASS, CritiqueVerdict.FLAG)

        message_map = {
            "WRITE_APPROVED": f"Memory write approved. Tag: {tag.value}.",
            "WRITE_FLAGGED": f"Memory write approved with flag. Review recommended. Tag: {tag.value}.",
            "WRITE_REJECTED": "Memory write BLOCKED. Critique found contradictions or plausibility issues.",
        }

        return ValidationResult(
            entry_id=entry.entry_id,
            audit_id=audit_id,
            approved=approved,
            epistemic_tag=tag,
            critique_verdict=verdict,
            critique_notes=critique_notes,
            latency_ms=round(latency_ms, 2),
            message=message_map[operation],
        )

    def query_audit(self, **kwargs) -> list:
        """Pass-through to AuditLogger.query for API/MCP access."""
        return self._audit.query(**kwargs)

    def get_session_summary(self, session_id: str) -> dict:
        """Pass-through to AuditLogger.get_session_summary."""
        return self._audit.get_session_summary(session_id)
