# chrysalis-interfaces

Protocol contracts and stub implementations for the Chrysalis accountability platform.

## What this package contains

- **Protocols.** Four `Protocol` classes (`Critic`, `CoherenceMonitor`, `AffectMonitor`, `Attester`) that define the surface between the open Memoir kernel and any production implementation.
- **Data classes.** `Belief`, `Turn`, `CritiqueResult`, `CoherenceScore`, `AffectScore`, `AttestationReceipt`, `Chain`. The shared vocabulary for all four interfaces.
- **Stubs.** `RuleBasedCritic`, `NoOpCoherenceMonitor`, `NoOpAffectMonitor`, `LocalLogAttester`. Minimal implementations that let the kernel run end to end with no external dependencies.

## Why interfaces

Chrysalis is open-core. The kernel ships fully open. Production implementations of the four pillars (Oracle, Mirror, Resonance, Shield) live in the closed Chrysalis platform. Anyone can also write their own implementation against the same contracts.

This package is the contract surface. Implementing one of the four protocols against any backend gives you a drop-in replacement for the closed module.

## Install

```bash
pip install chrysalis-interfaces
```

## Quick example

```python
from chrysalis_interfaces import (
    RuleBasedCritic,
    NoOpCoherenceMonitor,
    NoOpAffectMonitor,
    LocalLogAttester,
)

critic = RuleBasedCritic()
coherence = NoOpCoherenceMonitor()
affect = NoOpAffectMonitor()
attester = LocalLogAttester("./attestations.jsonl")
```

For the full pipeline that composes these into a working memory governance kernel, see [`chrysalis-kernel`](../memoir-kernel/).

## License

Apache License 2.0.

---

Make it a great day.
