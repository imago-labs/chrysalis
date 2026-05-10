# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at the root of this repository
# or at http://www.apache.org/licenses/LICENSE-2.0
"""
Chrysalis Interfaces

The four Protocol contracts that production implementations must satisfy.
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
    Turn,
    AffectScore,
)

from chrysalis_interfaces.stubs import (
    NoOpAffectMonitor,
    LocalLogAttester,
    NoOpCoherenceMonitor,
    RuleBasedCritic,
)

__all__ = [
    # Interfaces
    "AffectMonitor",
    "Attester",
    "CoherenceMonitor",
    "Critic",
    # Data classes
    "AffectScore",
    "AttestationReceipt",
    "Belief",
    "Chain",
    "CoherenceScore",
    "CritiqueResult",
    "Turn",
    # Stubs
    "NoOpAffectMonitor",
    "LocalLogAttester",
    "NoOpCoherenceMonitor",
    "RuleBasedCritic",
]
