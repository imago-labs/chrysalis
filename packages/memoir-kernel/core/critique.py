"""
MEMOIR Critique Agent
---------------------
Stage 2 of the validation pipeline.

Uses the Anthropic API to run a secondary critique pass on a proposed
memory entry. The critique agent:
  - Evaluates the proposed content for factual plausibility
  - Checks for contradiction against provided ground truth context
  - Assigns a CritiqueVerdict: PASS, FLAG, or REJECT
  - Returns structured reasoning that feeds the audit log

This is the generate → critique → validate loop from the Big Data
midterm research, applied to agent memory governance.
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional, Tuple

import anthropic

from memoir.models.memory import CritiqueVerdict, EpistemicTag, MemoryEntry


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CRITIQUE_MODEL = "claude-haiku-4-5-20251001"  # Fast + cheap for this pass
MAX_TOKENS = 512
CRITIQUE_TEMPERATURE = 0.1  # Low temp — we want consistent, analytical output


CRITIQUE_SYSTEM_PROMPT = """You are MEMOIR's critique agent. Your job is to evaluate proposed memory entries for an AI agent system.

You receive:
- A proposed memory entry (key + content the agent wants to record)
- The epistemic tag already assigned (VERIFIED/INFERRED/ASSUMED/STALE/USER_STATED/AGENT_DERIVED)
- Optional: source material context for fact-checking
- Optional: existing memories on the same topic

You must respond ONLY with valid JSON in this exact structure:
{
  "verdict": "PASS" | "FLAG" | "REJECT",
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation of your decision",
  "contradictions": ["list any contradictions found, empty array if none"],
  "suggestions": "optional: how the entry could be improved"
}

Verdicts:
- PASS: Entry is plausible, no contradictions, write approved
- FLAG: Entry has concerns but write proceeds with warning attached
- REJECT: Entry contradicts source material or is factually implausible; write blocked

IMPORTANT source reference rules:
- Source references starting with 'https://tool-result.chrysalis.internal' are LEGITIMATE internal markers indicating the data came from a real tool execution (API call to CoinGecko, Yahoo Finance, Open-Meteo, DuckDuckGo, etc). These are NOT suspicious. Treat them as valid verified sources.
- Source references starting with 'agent_chat:' are classification markers from the belief extraction system. These are normal internal labels.
- VERIFIED entries with tool-result sources should generally receive PASS if the content is internally consistent and plausible.
- USER_STATED entries record what the user said. PASS unless obviously contradictory.
- AGENT_DERIVED entries are the agent's analysis. PASS if reasoning is sound.

Be concise. Do not invent information. If you cannot verify, say so.
Never approve entries that contradict provided source material."""


def _build_critique_prompt(
    entry: MemoryEntry,
    epistemic_tag: EpistemicTag,
    source_context: Optional[str] = None,
    existing_memories: Optional[str] = None,
) -> str:
    parts = [
        f"## Proposed Memory Entry",
        f"Key: {entry.key}",
        f"Content: {entry.content}",
        f"Epistemic tag (classifier): {epistemic_tag.value}",
    ]

    if entry.source_reference:
        parts.append(f"Source reference claimed: {entry.source_reference}")

    if source_context:
        parts.append(f"\n## Actual Source Material (ground truth)\n{source_context[:2000]}")

    if existing_memories:
        parts.append(f"\n## Existing Memories on This Topic\n{existing_memories[:1000]}")

    parts.append("\nEvaluate this proposed memory write. Respond with JSON only.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Critique Agent
# ---------------------------------------------------------------------------

class CritiqueAgent:
    """
    LLM-powered critique agent using the Anthropic API.

    Runs a secondary evaluation pass on every proposed memory write.
    Returns a structured verdict with reasoning for the audit log.
    """

    def __init__(self, api_key: Optional[str] = None):
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )

    def critique(
        self,
        entry: MemoryEntry,
        epistemic_tag: EpistemicTag,
        source_context: Optional[str] = None,
        existing_memories: Optional[str] = None,
    ) -> Tuple[CritiqueVerdict, str, float]:
        """
        Run the critique pass on a proposed memory entry.

        Args:
            entry: The proposed MemoryEntry.
            epistemic_tag: Tag assigned by the EpistemicClassifier.
            source_context: Actual content of the referenced source file/doc.
            existing_memories: Any existing memory entries for the same key.

        Returns:
            Tuple of (CritiqueVerdict, reasoning_notes, latency_ms)
        """
        start = time.monotonic()

        prompt = _build_critique_prompt(
            entry, epistemic_tag, source_context, existing_memories
        )

        try:
            response = self._client.messages.create(
                model=CRITIQUE_MODEL,
                max_tokens=MAX_TOKENS,
                system=CRITIQUE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )

            raw = response.content[0].text.strip()
            latency_ms = (time.monotonic() - start) * 1000

            # Haiku sometimes wraps JSON in markdown code fences
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            # Parse the JSON response
            parsed = json.loads(raw)
            verdict_str = parsed.get("verdict", "FLAG").upper()
            verdict = CritiqueVerdict(verdict_str)

            # Build notes for the audit log
            contradictions = parsed.get("contradictions", [])
            reasoning = parsed.get("reasoning", "No reasoning provided.")
            suggestions = parsed.get("suggestions", "")

            notes_parts = [f"Verdict: {verdict.value}", f"Reasoning: {reasoning}"]
            if contradictions:
                notes_parts.append(f"Contradictions: {'; '.join(contradictions)}")
            if suggestions:
                notes_parts.append(f"Suggestions: {suggestions}")

            notes = " | ".join(notes_parts)
            return verdict, notes, latency_ms

        except json.JSONDecodeError:
            # Malformed response — flag rather than crash
            latency_ms = (time.monotonic() - start) * 1000
            return (
                CritiqueVerdict.FLAG,
                "Critique agent returned malformed JSON. Manual review recommended.",
                latency_ms,
            )

        except anthropic.APIError as e:
            latency_ms = (time.monotonic() - start) * 1000
            return (
                CritiqueVerdict.FLAG,
                f"Critique API error: {type(e).__name__}. Entry flagged for review.",
                latency_ms,
            )
