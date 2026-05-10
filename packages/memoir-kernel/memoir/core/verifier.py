# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at the root of this repository
# or at http://www.apache.org/licenses/LICENSE-2.0
"""
MEMOIR Ground Truth Verifier
-----------------------------
Stage 3 of the validation pipeline (VERIFIED entries only).

For entries the classifier tagged VERIFIED — meaning they claim a concrete
source reference — this verifier reads the actual source and checks whether
the memory content is consistent with it.

Supports local file paths and future extension for URLs / RAG sources.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

from memoir.models.memory import MemoryEntry


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------

class GroundTruthVerifier:
    """
    Reads the source reference claimed by a VERIFIED memory entry
    and returns its content for the critique agent to compare against.

    This is intentionally simple: it retrieves content, not judges it.
    Judgment is the critique agent's job. The verifier's job is to make
    sure there *is* something real to compare against.
    """

    def __init__(self, workspace_root: Optional[str] = None):
        """
        Args:
            workspace_root: The root directory for resolving relative file paths.
                            Defaults to the current working directory.
        """
        self._workspace_root = Path(workspace_root or os.getcwd())

    def fetch_source_context(
        self,
        entry: MemoryEntry,
        max_chars: int = 3000,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Attempt to read the source material referenced by a memory entry.

        Args:
            entry: MemoryEntry with a source_reference to resolve.
            max_chars: Maximum characters to return (avoid token bloat).

        Returns:
            Tuple of (source_content, error_message).
            source_content is None if the source could not be read.
            error_message is None on success.
        """
        if not entry.source_reference:
            return None, "No source reference provided."

        ref = entry.source_reference.strip()

        # Internal tool-result URLs are pre-verified by the agent's tool
        # execution layer. The data already came from a real API call
        # (CoinGecko, Yahoo Finance, Open-Meteo, etc). No fetch needed.
        if "tool-result.chrysalis.internal" in ref:
            return f"Tool-verified data. Source: {ref}", None

        # Agent chat sources carry the extraction tag -- these are
        # classification markers, not fetchable URLs
        if ref.startswith("agent_chat:"):
            return None, None

        # Local file path
        if self._looks_like_file_path(ref):
            return self._read_local_file(ref, max_chars)

        # URL -- placeholder for future implementation
        if ref.startswith("http://") or ref.startswith("https://"):
            return None, f"URL sources not yet supported: {ref}"

        # Line reference (#L42) — needs a file context we don't have standalone
        if ref.startswith("#L"):
            return None, f"Line references require a file path prefix: {ref}"

        return None, f"Unrecognized source reference format: {ref}"

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _looks_like_file_path(self, ref: str) -> bool:
        """Heuristic: does this look like a file path?"""
        # Has a file extension or starts with ./ or /
        return (
            "." in Path(ref).name
            or ref.startswith("./")
            or ref.startswith("/")
            or ref.startswith("~/")
        )

    def _read_local_file(
        self, ref: str, max_chars: int
    ) -> Tuple[Optional[str], Optional[str]]:
        """Read a local file, resolving relative paths against workspace root."""
        # Expand home directory
        path = Path(ref).expanduser()

        # If relative, resolve against workspace root
        if not path.is_absolute():
            path = self._workspace_root / path

        if not path.exists():
            return None, f"Source file not found: {path}"

        if not path.is_file():
            return None, f"Source reference is not a file: {path}"

        # Restrict to text files for now
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            if len(content) > max_chars:
                content = content[:max_chars] + f"\n...[truncated at {max_chars} chars]"
            return content, None
        except OSError as e:
            return None, f"Could not read source file: {e}"
