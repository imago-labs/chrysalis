# memoir-kernel

The four-stage belief validation pipeline and audit log. The open-source heart of Chrysalis.

## What this package contains

| Subpackage | Purpose |
|---|---|
| `core/` | The four-stage validation pipeline (heuristic, semantic, conflict, attestation) and the orchestrator |
| `conflicts/` | Conflict detection: contradicts existing belief, contradicts pinned constraint, exceeds confidence threshold |
| `provenance/` | Tamper-evident audit log with full provenance graph |
| `models/` | SQLAlchemy models for beliefs, audit-log entries, agents |
| `api/` | FastAPI surface for hosted Memoir |

## How it composes the four interfaces

The kernel is interface-driven. It accepts any implementation of the four `chrysalis-interfaces` protocols and orchestrates them into a working memory governance pipeline.

```python
from chrysalis_interfaces import (
    RuleBasedCritic,
    NoOpCoherenceMonitor,
    NoOpAffectMonitor,
    LocalLogAttester,
)
from memoir_kernel import Memoir

memoir = Memoir(
    critic=RuleBasedCritic(),
    coherence_monitor=NoOpCoherenceMonitor(),
    affect_monitor=NoOpAffectMonitor(),
    attester=LocalLogAttester("./audit.jsonl"),
)

result = memoir.validate_belief(belief)
if result.committed:
    print("Belief committed:", result.audit_log_id)
```

## Why this is open

The kernel adds value through composability, not through proprietary scoring. Anyone can run the kernel locally with stubs, plug in their own implementations of the four interfaces, or upgrade to the closed Chrysalis platform implementations through Chrysalis Cloud.

## Install

```bash
# Coming with first alpha release.
pip install memoir-kernel
```

## License

Apache License 2.0.

---

Make it a great day.
