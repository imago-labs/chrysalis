"""
Chrysalis quickstart example.

Demonstrates a minimal Memoir pipeline using stub implementations of the four
Chrysalis interface protocols. No API key, no external services required.

Run:
    python run.py
"""

from datetime import datetime, timezone

from chrysalis_interfaces import (
    Belief,
    LocalLogAttester,
    NoOpAffectMonitor,
    NoOpCoherenceMonitor,
    RuleBasedCritic,
)


def main() -> None:
    critic = RuleBasedCritic()
    coherence = NoOpCoherenceMonitor()
    affect = NoOpAffectMonitor()
    attester = LocalLogAttester("./attestations.jsonl")

    print("Chrysalis quickstart")
    print("====================")
    print(f"Critic:           {type(critic).__name__}")
    print(f"CoherenceMonitor: {type(coherence).__name__}")
    print(f"AffectMonitor:    {type(affect).__name__}")
    print(f"Attester:         {type(attester).__name__}")
    print()

    belief = Belief(
        agent_id="quickstart_agent",
        content="The user prefers concise answers.",
        embedding=[0.0] * 768,
        source_context="quickstart_session turn 1",
        timestamp=datetime.now(timezone.utc),
        metadata={"example": True},
    )

    critique = critic.critique(belief)
    print(f"Belief Quality Score: {critique.belief_quality_score:.2f}")
    print(f"Issues found:         {critique.issues_found or 'none'}")
    print(f"Reasoning:            {critique.reasoning}")
    print()

    receipt = attester.attest(b"quickstart_payload", chain="local")
    print(f"Attestation written: {receipt.transaction_id[:16]}...")
    print(f"See attestations.jsonl for the local audit log.")


if __name__ == "__main__":
    main()
