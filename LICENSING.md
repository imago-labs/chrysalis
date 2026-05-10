# Licensing

Chrysalis is split into an open kernel and a closed platform. This document explains what is in which repository, what license applies to each, and what that means for users and contributors.

## Open kernel: this repository

Everything in this repository (`imago-labs/chrysalis`) is licensed under the **Apache License, Version 2.0**.

The open kernel includes:

- `packages/memoir-kernel/`: the audit-logged memory layer, conflict resolution, provenance tracking.
- `packages/chrysalis-interfaces/`: Protocol contracts (`Critic`, `CoherenceMonitor`, `AffectMonitor`, `Attester`) and reference stubs (`RuleBasedCritic`, `NoOpCoherenceMonitor`, `NoOpAffectMonitor`, `LocalLogAttester`).
- `packages/chrysalis-mcp/`, `packages/chrysalis-receipts/`, `packages/chrysalis-sdk/`: adapters and SDKs.
- `examples/`, `tests/`, `ARCHITECTURE.md`, and all surrounding documentation.

You can use, modify, distribute, and build commercial products on top of any of this code under the terms of Apache 2.0. See [LICENSE](LICENSE) for the full text.

The Apache 2.0 license also includes an explicit patent grant. Contributors grant a license to any patent claims they hold that are necessarily infringed by their contribution.

## Closed platform: separate repository

The production cognitive layer that ships on top of Chrysalis is maintained in a separate, private repository (`imago-labs/chrysalis-platform`). It is **not** licensed under Apache 2.0.

The closed platform includes:

- ORACLE. LLM-driven self-reflective critic.
- MIRROR: coherence-based hallucination detection.
- RESONANCE: affect-aware behavioral signaling.
- SHIELD. Solana on-chain provenance attestation.
- The hosted dashboard and marketing site.

These modules are All Rights Reserved, © 2026 Imago Labs / Metamorphic Curations LLC. They are consumed by the open kernel only through the Protocol interface contracts in `packages/chrysalis-interfaces/`. The open kernel ships safe rule-based and no-op stubs that satisfy those contracts so the kernel remains fully functional on its own.

If you want commercial access to the closed platform, contact hello@imagolabs.dev.

## Aletheia

The Aletheia accountability protocol has its own split:

- `imago-labs/aletheia`: public Solidity contracts, protocol spec, deploy scripts. Apache 2.0.
- `imago-labs/aletheia-platform`: private backend, frontend, calculator, demo. All Rights Reserved.

## Contributing

By submitting a pull request to this repository, you agree to the terms in [CONTRIBUTOR_LICENSE_AGREEMENT.md](CONTRIBUTOR_LICENSE_AGREEMENT.md). The CLA confirms that you have the right to contribute the code and that you license it under Apache 2.0 along with the rest of the repository.

## Trademarks

"Chrysalis", "Imago Labs", "ORACLE", "MIRROR", "RESONANCE", "SHIELD", and "Aletheia" are trademarks of Metamorphic Curations LLC. The Apache 2.0 license does not grant trademark rights. See [TRADEMARK.md](TRADEMARK.md) for usage guidelines.

## Questions

For licensing questions, commercial inquiries, or trademark usage, contact hello@imagolabs.dev.
