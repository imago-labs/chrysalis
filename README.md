# Chrysalis

> **The accountability layer for autonomous AI.**
> Memory, oversight, and on-chain provenance for the agentic economy.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-pre--alpha-orange.svg)](#status)

Chrysalis is an open kernel for agent accountability. It makes autonomous AI agents demonstrably honest about what they believe, how they behave, and what they do.

This repository contains the public open-source kernel (Apache 2.0). The production-grade implementations of Oracle, Mirror, Resonance, and Shield modules live in a separate, closed-source platform repository and are consumed via the [Chrysalis Cloud API](#) or licensed self-hosted deployment.

For the full architecture, see [`ARCHITECTURE.md`](./ARCHITECTURE.md).

---

## Status

🟠 **Pre-alpha. Under active development.**
APIs will change. Do not use in production yet. Star the repo to follow.

The first stable release will ship the Memoir kernel with stable interfaces for Critic, CoherenceMonitor, AffectMonitor, and Attester implementations.

---

## What's in this repo

| Package | Purpose |
|---|---|
| `packages/memoir-kernel/` | The four-stage belief validation pipeline and audit log |
| `packages/chrysalis-interfaces/` | Critic, CoherenceMonitor, AffectMonitor, Attester protocol contracts |
| `packages/chrysalis-sdk/` | Python and TypeScript client SDKs |
| `packages/chrysalis-mcp/` | Model Context Protocol server (Claude Code integration) |
| `packages/chrysalis-receipts/` | Open Memory Receipts spec and reference verifier |
| `examples/` | Runnable demo agents using stub implementations |
| `benchmarks/` | Adversarial Memory benchmark harness (dataset published separately) |
| `docs/` | Documentation site source |

---

## Quick start

```bash
# Coming with first alpha release.
pip install chrysalis-kernel
```

```python
# Coming with first alpha release.
from chrysalis import Memoir, RuleBasedCritic, NoOpCoherenceMonitor

memoir = Memoir(
    critic=RuleBasedCritic(),
    coherence_monitor=NoOpCoherenceMonitor(),
)
result = memoir.validate_belief(belief)
```

---

## The platform vs the kernel

Chrysalis follows an open-core model.

**This repo (Apache 2.0):** Memoir kernel, interface contracts, SDKs, MCP server, Memory Receipts open standard, and stub implementations sufficient to run demos locally.

**Chrysalis Cloud (closed):** Production Oracle, Mirror, Resonance, and Shield implementations. Hosted, multi-tenant, with dashboard, audit, and enterprise compliance features. Free tier available.

**Chrysalis Enterprise (licensed):** Self-hosted Docker deployment of the full closed stack for customers requiring on-premise operation.

The kernel in this repo is genuinely useful on its own with stub implementations. Real value comes from plugging in production critics, monitors, and attesters from Chrysalis Cloud, but it is not required.

---

## Built on Chrysalis

Chrysalis is the platform that powers:

- **[Aletheia Protocol](https://www.aletheiaprotocol.io)**. Parametric on-chain protection for the agentic economy. The first vertical product built on Chrysalis. ([repo](https://github.com/imago-labs/aletheia))

---

## Contributing

🟠 **Not yet accepting external contributions.** A Contributor License Agreement and contribution guidelines will be published with the first alpha release. To follow progress, watch this repo or join the discussions tab.

---

## About

Chrysalis is built and maintained by [Imago Labs](https://github.com/imago-labs), the research arm of Metamorphic Curations LLC.

Founded by [Crystal Tubbs](https://github.com/Msmetamorphosis).

---

## License and governance

Apache License 2.0. See [`LICENSE`](./LICENSE) for the full text.

- [`LICENSING.md`](./LICENSING.md): what is open, what is closed, and how the two relate.
- [`TRADEMARK.md`](./TRADEMARK.md): what you can and cannot do with the Chrysalis name and marks.
- [`CONTRIBUTOR_LICENSE_AGREEMENT.md`](./CONTRIBUTOR_LICENSE_AGREEMENT.md): terms for code contributions.
- [`SECURITY.md`](./SECURITY.md): how to report a vulnerability.

---

Make it a great day.
