# chrysalis-receipts

The Memory Receipts open standard. Portable, signed, verifiable credentials that prove an agent's epistemic provenance to other systems.

## Why Memory Receipts

Today, when an agent moves between platforms, its memory and reputation do not. A Memory Receipt is a signed credential that proves:

- This agent was governed by Chrysalis at the time the receipt was issued
- These specific beliefs were validated and committed to its audit log
- The audit log is anchored on chain and verifiable without trusting the issuer

This package contains the open spec, reference verifier, and example credentials. The signing implementation lives in the closed Shield module of the Chrysalis platform.

## Spec

A Memory Receipt is a JSON object signed by the issuing Chrysalis instance.

```json
{
  "version": "1",
  "issuer": "did:chrysalis:imago-labs",
  "subject": {
    "agent_id": "agent_abc123",
    "identity_hash": "0x..."
  },
  "audit_log_range": {
    "merkle_root": "0x...",
    "from_index": 0,
    "to_index": 14829
  },
  "issued_at": "2026-05-09T18:42:00Z",
  "expires_at": "2027-05-09T18:42:00Z",
  "anchors": [
    { "chain": "solana", "tx": "..." },
    { "chain": "base",   "tx": "..." }
  ],
  "signature": "0x..."
}
```

## What's in this package

- `spec/SCHEMA.md`. Full schema specification (forthcoming with first alpha)
- `verifier/`. Reference verifier (Python and TypeScript) for offline credential verification
- `examples/`. Example receipts, both valid and intentionally tampered, for verifier testing

## License

Apache License 2.0. The Memory Receipts spec itself is intended for broad adoption and is unencumbered.

---

Make it a great day.
