# Chrysalis. Architecture Overview

> **Status:** Draft for internal review. Not yet published. May 2026.

Chrysalis is an open-core platform for agent accountability. It makes autonomous AI agents demonstrably honest about what they believe, how they behave, and what they do, at the cognitive, behavioral, and financial layers of the agent stack.

Chrysalis is built and stewarded by [Imago Labs](https://github.com/imago-labs).

## The thesis

Autonomous AI agents are increasingly trusted with consequential decisions in finance, healthcare, legal, and infrastructure domains. They are also demonstrably capable of:

- Confabulating beliefs that drift from their training and constraints
- Capitulating to user pressure (sycophancy, frustration-driven compliance)
- Executing transactions that exceed their registered authority
- Producing outputs whose epistemic provenance is unverifiable

Existing agent infrastructure does not address these failure modes systemically. Memory frameworks store but do not validate. Orchestration frameworks coordinate but do not govern. On-chain attestation projects record outputs but not the epistemic state that produced them.

Chrysalis closes that gap with a layered accountability architecture.

## The architecture

Chrysalis spans three layers of the agent stack, with five horizontal modules that any agent in any vertical can consume, plus one flagship vertical product (Aletheia) that demonstrates the platform end-to-end in the financial domain.

```
                          ┌─────────────────────────────────┐
                          │            CHRYSALIS            │
                          │    Agent Accountability Layer   │
                          └─────────────────────────────────┘
                                          │
              ┌───────────────────────────┼───────────────────────────┐
              │                           │                           │
      ┌───────▼────────┐         ┌────────▼─────────┐        ┌────────▼────────┐
      │   COGNITIVE    │         │   BEHAVIORAL     │        │    FINANCIAL    │
      │  what an agent │         │  how an agent    │        │  what an agent  │
      │    believes    │         │   is acting      │        │     spends      │
      └────────────────┘         └──────────────────┘        └─────────────────┘
              │                           │                           │
   ┌──────────┴──────────┐      ┌─────────┴─────────┐                 │
   │                     │      │                   │                 │
   ▼                     ▼      ▼                   ▼                 ▼
┌────────┐          ┌────────┐ ┌────────┐      ┌─────────┐      ┌──────────┐
│ MEMOIR │          │ ORACLE │ │ MIRROR │      │RESONANCE│      │  SHIELD  │
└────────┘          └────────┘ └────────┘      └─────────┘      └──────────┘
                                                                      │
                                                              built on top of
                                                                      │
                                                              ┌───────▼──────┐
                                                              │   ALETHEIA   │
                                                              │  (flagship   │
                                                              │   product)   │
                                                              └──────────────┘
```

### The five horizontal modules

**MEMOIR. Memory governance kernel.**
Four-stage validation pipeline (heuristic, semantic, conflict, attestation) that runs every belief an agent attempts to commit. Maintains a tamper-evident audit log with full provenance graph. The only fully open-source module. Everything else plugs into it through declared interfaces.

**ORACLE. Self-reflective learning loop.**
LLM-powered critic that scores belief quality (BQS), detects drift patterns in the audit log over time, and produces structured insights the agent can use to improve. Implements the `Critic` interface defined by Memoir.

**MIRROR. Behavioral coherence monitor.**
Continuously scores agent output along four dimensions (Linguistic Confidence Index, Response Length Deviation, Semantic Coherence Score, Hedge Escalation Rate) and fuses nine signals into a Cognitive Pressure Index. Triggers interventions when CPI breaches threshold. Implements the `CoherenceMonitor` interface.

**RESONANCE. User-affect to agent-behavior coupling.**
Detects coupling between user emotional state and agent epistemic state. A measurable, on-chain-attestable behavioral signal. Computes Pearson correlation between user-sentiment-deltas (MCPL) and agent-behavior-deltas (BEM) over rolling windows. Implements the `AffectMonitor` interface.

**SHIELD. Cross-chain attestation layer.**
Anchors Memoir attestations to Solana (cheap, fast) and registers agent identity on Base (USDC-native, EVM standard). Issues portable Memory Receipts: signed, verifiable credentials that prove an agent's epistemic provenance to other systems. Implements the `Attester` interface.

### The flagship vertical product

**ALETHEIA. Parametric on-chain protection for financial agents.**
A standalone product, built on Chrysalis, that provides discretionary mutual protection coverage for verifiable financial breaches by autonomous agents. Today: spend cap breach (Cat 1, live on Base Sepolia). Roadmap: slippage breach (Cat 2), liquidation breach (Cat 3). Aletheia uses Chrysalis's Shield for attestation, Mirror's CPI for dynamic risk pricing, and Memoir's audit log as part of claim evidence.

Aletheia is operated as a separate product line with its own brand, site ([aletheiaprotocol.io](https://www.aletheiaprotocol.io)), and pool economics. It is the canonical demonstration that the Chrysalis platform delivers real-world value, not just abstraction.

Future products on Chrysalis are possible (compliance audit, governance SaaS, memory receipt credential exchange) but uncommitted.

## Repository layout

The platform is split across **four repositories** with deliberate licensing:

| Repo | Visibility | License | Purpose |
|---|---|---|---|
| [`imago-labs/chrysalis`](https://github.com/imago-labs/chrysalis) | Public | Apache 2.0 | Memoir kernel, interfaces, SDKs, MCP server, Memory Receipts spec |
| [`imago-labs/chrysalis-platform`](https://github.com/imago-labs/chrysalis-platform) | Private | All Rights Reserved | Production Oracle, Mirror, Resonance, Shield implementations, benchmarks, compliance mappings |
| [`imago-labs/aletheia`](https://github.com/imago-labs/aletheia) | Public | Apache 2.0 | Aletheia smart contracts, ABIs, deployment scripts, protocol spec |
| [`imago-labs/aletheia-platform`](https://github.com/imago-labs/aletheia-platform) | Private | All Rights Reserved | Aletheia off-chain: Bedrock arbitrator, x402 payments, frontend, dashboard |

### Why this split

**Open** the things whose value depends on adoption (the kernel, the protocol contracts, the receipt standard, the integration surfaces). **Close** the things whose value depends on protection (the production-grade implementations, the benchmark dataset, the compliance content, the platform dashboards).

This follows the open-core pattern adopted by Letta, PostHog, Supabase, and others. The community gets a genuinely useful free thing. The company captures value on the closed implementations and managed offering.

### How the open kernel and closed platform connect

The public Chrysalis kernel ships **interface contracts** and **stub implementations**:

```python
# chrysalis/packages/chrysalis-interfaces/protocols.py  (PUBLIC)
class Critic(Protocol):
    def critique(self, belief: Belief) -> CritiqueResult: ...

class CoherenceMonitor(Protocol):
    def score(self, turn: Turn) -> CoherenceScore: ...

class AffectMonitor(Protocol):
    def score(self, user_turn: str, agent_turn: str) -> AffectScore: ...

class Attester(Protocol):
    def attest(self, payload: bytes, chain: Chain) -> AttestationReceipt: ...
```

The public kernel includes **stub implementations** sufficient to demo the architecture locally:

- `RuleBasedCritic`. Heuristic stub for `Critic`.
- `NoOpCoherenceMonitor`. Returns neutral scores. Placeholder for `CoherenceMonitor`.
- `NoOpAffectMonitor`. Placeholder for `AffectMonitor`.
- `LocalLogAttester`. Writes to a local JSONL file. Placeholder for `Attester`.

The private platform ships the **real implementations** that meaningfully fill those interfaces:

- `ChrysalisOracleCritic`. Real LLM-driven critique.
- `MirrorCoherenceMonitor`. Full BEM and CPI scoring.
- `ResonanceAffectMonitor`. Full MCPL and RCS coupling.
- `SolanaAttester`, `BaseAttester`, `CrossChainAttester`. Real on-chain anchoring.

Customers consume the platform via:

1. **Hosted API.** `pip install chrysalis-cloud` thin client. Free tier with limits, paid above.
2. **Self-hosted enterprise.** Licensed Docker image with the closed stack bundled. Runs in customer infrastructure behind a license key.

## The unification: Aletheia as a Chrysalis-built product

Aletheia and Chrysalis were initially developed as separate projects. As of May 2026 they are consolidated under Imago Labs with the following architecture:

1. **Aletheia's `registerAgent`** accepts a `chrysalisIdentityHash` containing the agent's belief-state fingerprint, audit-log Merkle root, and tool manifest hash. Agent identity in Aletheia is now **epistemic**, not just wallet-based.
2. **Mirror's CPI** flows into Aletheia's risk pricing as a dynamic input: `riskScore_t = f(baseRisk, currentCPI_t, currentRCS_t)`. Agents whose coherence stays high earn lower premiums. Agents drifting toward instability pay more or are flagged for re-registration.
3. **Resonance's RCS-CRITICAL events** trigger pre-claim risk monitoring. Sustained sycophancy and frustration coupling is a leading indicator of breach.
4. **Memoir's audit log** is part of claim evidence. A breach claim now includes the on-chain tx hash plus the Memoir audit-log range that produced the action. Court-grade reconstruction of agent state at the moment of the breach.
5. **Shield** routes attestations to the appropriate chain: Memoir attestations to Solana (cheap), Aletheia registry and coverage events to Base (USDC-native). One unified `attest()` API, one cross-chain agent identity.

## Pluggable verifier architecture (Aletheia's expansion path)

Aletheia today verifies one risk category (spend cap breach) using one arbitrator (Claude on Bedrock). The architecture supports many arbitrators, each evaluating their own harm category against their own evidentiary standard:

```
AletheiaPool (settlement layer)
        │
        ├── Cat 1: Spend Cap Breach    → BedrockSpendCapArbitrator (LIVE)
        ├── Cat 2: Slippage Breach     → SlippageArbitrator (Q3 2026)
        ├── Cat 3: Liquidation Breach  → LiquidationArbitrator (Q3 2026)
        └── Cat N: <future categories> → community-built arbitrators
```

The pool, claim bond, oracle resolution, USDC payout, and on-chain registry are reusable across all arbitrators. Verifiers plug in via a standard interface defined in the public Aletheia protocol repo. This separates **settlement** (hard, reusable, well-built) from **verification** (specific, varies by harm category, can be built by partners).

## What this means strategically

For Imago Labs:

- **One brand, one focus, one fundraise.** Chrysalis is the platform. Aletheia is the flagship product. No more confusion about whether they're the same company or different.
- **Open kernel as the wedge** for adoption, hiring credibility, and contributor pipeline. Closed platform as the moat.
- **Pluggable verifier architecture** lets Aletheia expand harm coverage without Imago Labs solving every fraud-resistance problem alone.
- **Cross-chain Memory Receipts** as an open standard positions Imago Labs as the steward of agent provenance, not just the operator of one tool.

---

This document is the canonical architecture reference for the Chrysalis platform. It will be kept in sync with the codebase as modules ship. Updates should be reviewed by Crystal Tubbs (Imago Labs) before merging.

Make it a great day.
