# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at the root of this repository
# or at http://www.apache.org/licenses/LICENSE-2.0
"""
Chrysalis Interfaces

The Protocol contracts that production implementations must satisfy.
The open kernel ships stub implementations of each. The closed Chrysalis
platform ships production implementations.

See ARCHITECTURE.md for the full architecture.
"""

from chrysalis_interfaces.protocols import (
    AffectMonitor,
    Attester,
    AttestationReceipt,
    Belief,
    Chain,
    CoherenceMonitor,
    CoherenceScore,
    Critic,
    CritiqueResult,
    InferenceMessage,
    InferenceResponse,
    Inferencer,
    ToolCall,
    ToolSpec,
    Turn,
    AffectScore,
)

from chrysalis_interfaces.stubs import (
    NoOpAffectMonitor,
    LocalLogAttester,
    NoOpCoherenceMonitor,
    RuleBasedCritic,
)

# Reference Inferencer adapters are import-on-demand to avoid requiring httpx
# at import time for users who only need the Protocol contracts.


def __getattr__(name: str):
    if name in ("OllamaInferencer", "VLLMInferencer"):
        from chrysalis_interfaces import inferencers

        return getattr(inferencers, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    # Interfaces
    "AffectMonitor",
    "Attester",
    "CoherenceMonitor",
    "Critic",
    "Inferencer",
    # Data classes
    "AffectScore",
    "AttestationReceipt",
    "Belief",
    "Chain",
    "CoherenceScore",
    "CritiqueResult",
    "InferenceMessage",
    "InferenceResponse",
    "ToolCall",
    "ToolSpec",
    "Turn",
    # Stubs
    "NoOpAffectMonitor",
    "LocalLogAttester",
    "NoOpCoherenceMonitor",
    "RuleBasedCritic",
    # Reference Inferencers (lazy-imported, need httpx)
    "OllamaInferencer",
    "VLLMInferencer",
]
