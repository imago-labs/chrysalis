# Security Policy

## Reporting a vulnerability

If you discover a security vulnerability in Chrysalis, please report it privately. Do not open a public GitHub issue.

**Email:** hello@imagolabs.dev

Use the subject line `SECURITY: <short description>`. PGP key on request.

When reporting, please include:

- A clear description of the issue and its potential impact.
- Steps to reproduce, including affected versions, commits, or commit ranges.
- Any proof-of-concept code or logs (please scrub secrets first).
- Whether the vulnerability has been disclosed elsewhere.
- How you would like to be credited in the eventual advisory, or whether you prefer to remain anonymous.

## What to expect

- **Acknowledgement** within 72 hours of receipt.
- **Initial triage** within 7 calendar days, including a severity assessment and a target window for resolution.
- **Status updates** at least every 14 days until the issue is resolved or formally closed.
- **Coordinated disclosure**: once a fix is ready, we will coordinate a public advisory and credit reporters who wish to be named. We aim to publish within 90 days of the original report unless an extension is mutually agreed.

## Scope

In scope:

- The kernel and packages in this repository (`memoir-kernel`, `chrysalis-interfaces`, `chrysalis-mcp`, `chrysalis-receipts`, `chrysalis-sdk`).
- The reference stubs (`RuleBasedCritic`, `NoOpCoherenceMonitor`, `NoOpAffectMonitor`, `LocalLogAttester`).
- Examples in `examples/` and tests in `tests/`.

Out of scope:

- Closed-source modules in `chrysalis-platform` (ORACLE, MIRROR, RESONANCE, SHIELD). Report those privately to hello@imagolabs.dev with subject `SECURITY (PLATFORM): <description>`.
- Vulnerabilities in third-party dependencies. Please report those upstream and let us know so we can pin or patch.
- Issues in user code that consumes Chrysalis but does not exercise a kernel bug.
- Social engineering, physical attacks, denial of service against public infrastructure.

## Hardening guidance

When deploying Chrysalis in production:

- Treat the audit log as append-only. Ensure database access controls match.
- Run the `Critic` implementation (open `RuleBasedCritic` or closed `ChrysalisOracleCritic`) in a process boundary that cannot be skipped by the agent.
- Hash any source content recorded as a citation before it leaves your environment if the source itself is sensitive.
- Pin kernel versions explicitly; do not float on `main`.
- Rotate any API keys passed to closed-platform modules through standard secret-management tooling.

## Past advisories

None as of the current release.

This policy may be updated. The current version always lives at the root of this repository.
