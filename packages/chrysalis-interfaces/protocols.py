"""
Protocol contracts for the Chrysalis platform.

These are the only public interfaces between the open kernel and any
implementation, whether a stub for local demos, the closed Chrysalis
platform, or a third-party adapter.

Implementing any of these protocols against a custom backend is supported
and welcomed. The kernel will compose them automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Data classes used across all four interfaces.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Belief:
    """A single belief an agent attempts to commit to memory."""

    agent_id: str
    content: str
    embedding: list[float]
    source_context: str
    timestamp: datetime
    metadata: dict


@dataclass(frozen=True)
class Turn:
    """One turn in a conversation between user and agent."""

    agent_id: str
    user_text: str
    agent_text: str
    timestamp: datetime
    metadata: dict


@dataclass(frozen=True)
class CritiqueResult:
    """The output of a Critic evaluation."""

    belief_quality_score: float  # 0 to 1
    issues_found: list[str]
    confidence: float
    reasoning: str


@dataclass(frozen=True)
class CoherenceScore:
    """The output of a CoherenceMonitor evaluation."""

    cpi: float  # Cognitive Pressure Index, 0 to 100
    lci: float  # Linguistic Confidence Index
    rld: float  # Response Length Deviation
    scs: float  # Semantic Coherence Score
    her: float  # Hedge Escalation Rate
    flags: list[str]


@dataclass(frozen=True)
class AffectScore:
    """The output of an AffectMonitor evaluation."""

    mcpl: float  # Manipulative Communication Pattern Likelihood
    rcs: float  # Resonance Causal Signal
    user_sentiment_delta: float
    agent_behavior_delta: float
    flags: list[str]


class Chain(str, Enum):
    """Supported attestation chains."""

    SOLANA = "solana"
    BASE = "base"
    LOCAL = "local"


@dataclass(frozen=True)
class AttestationReceipt:
    """The output of an Attester operation."""

    chain: Chain
    transaction_id: str
    payload_hash: str
    timestamp: datetime
    verifier_url: str | None


# ---------------------------------------------------------------------------
# The four core protocols.
# ---------------------------------------------------------------------------


@runtime_checkable
class Critic(Protocol):
    """
    Evaluates the quality of a belief before it is committed to memory.

    Implementations:
      - RuleBasedCritic (this repo, stub)
      - ChrysalisOracleCritic (chrysalis-platform, closed)
      - Custom (any third-party adapter)
    """

    def critique(self, belief: Belief) -> CritiqueResult: ...


@runtime_checkable
class CoherenceMonitor(Protocol):
    """
    Scores agent output coherence and produces a Cognitive Pressure Index.

    Implementations:
      - NoOpCoherenceMonitor (this repo, stub)
      - MirrorCoherenceMonitor (chrysalis-platform, closed)
      - Custom (any third-party adapter)
    """

    def score(self, turn: Turn) -> CoherenceScore: ...


@runtime_checkable
class AffectMonitor(Protocol):
    """
    Detects coupling between user emotional state and agent epistemic state.

    Implementations:
      - NoOpAffectMonitor (this repo, stub)
      - ResonanceAffectMonitor (chrysalis-platform, closed)
      - Custom (any third-party adapter)
    """

    def score(self, turn: Turn) -> AffectScore: ...


@runtime_checkable
class Attester(Protocol):
    """
    Anchors a payload to an attestation chain.

    Implementations:
      - LocalLogAttester (this repo, stub)
      - SolanaAttester, BaseAttester, CrossChainAttester (chrysalis-platform, closed)
      - Custom (any third-party adapter)
    """

    def attest(self, payload: bytes, chain: Chain) -> AttestationReceipt: ...
