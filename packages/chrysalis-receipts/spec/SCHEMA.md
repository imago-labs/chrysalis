# Memory Receipts Specification, v0.1

Status: Draft, May 2026. Subject to change before v1.0.
Maintainer: Imago Labs.
License: Apache License 2.0. The spec is intended for unencumbered adoption.

## 1. What is a Memory Receipt

A Memory Receipt is a signed, portable credential issued by a Chrysalis-governed runtime that attests to three things about an autonomous agent:

1. The agent was operating under Chrysalis governance during a specific window.
2. A specific range of the agent's audit log, identified by a Merkle root, contains the beliefs the receipt describes.
3. That audit log range was anchored on one or more public chains, so its contents cannot be altered after issuance without detection.

Receipts let an agent carry its provenance with it across platforms. A receiver does not need to call back to the issuer to verify that an agent has a clean audit history; the receipt is verifiable offline given the issuer's public key and a chain client.

A receipt is not an authorization token. It does not grant the agent access to anything. It is a portable claim of past behavior.

## 2. Document type

A Memory Receipt is a JSON object that conforms to the [W3C Verifiable Credentials Data Model v2.0](https://www.w3.org/TR/vc-data-model-2.0/) profile defined in section 4. JSON-LD framing is permitted but not required at v0.1; a plain-JSON receipt is the canonical form.

## 3. Required fields

All fields below are required unless explicitly marked optional.

| Field | Type | Description |
|---|---|---|
| `version` | string | Spec version. Must be `"1"` for receipts conforming to this draft. |
| `issuer` | string | Decentralized identifier of the issuing runtime. SHOULD be a DID. v0.1 reserves the `did:chrysalis:<slug>` method. |
| `subject.agent_id` | string | Opaque identifier the issuer uses to reference the agent. |
| `subject.identity_hash` | string | 0x-prefixed SHA-256 hash of the agent's identity material. Lets the receiver bind the receipt to a specific agent without exposing the underlying identity. |
| `audit_log_range.merkle_root` | string | 0x-prefixed Merkle root of the audit log range the receipt covers. SHOULD be computed using the canonical record encoding defined in section 6. |
| `audit_log_range.from_index` | integer | First audit log index covered, inclusive. Zero-based. |
| `audit_log_range.to_index` | integer | Last audit log index covered, inclusive. |
| `issued_at` | string | RFC 3339 timestamp of issuance, in UTC with a `Z` suffix. |
| `expires_at` | string | RFC 3339 timestamp after which receivers SHOULD treat the receipt as stale. |
| `anchors` | array | One or more on-chain anchors of the Merkle root. See section 7. |
| `signature` | string | 0x-prefixed signature over the canonical payload. See section 8. |

### 3.1 Optional fields

| Field | Type | Description |
|---|---|---|
| `attributes` | object | Issuer-defined attributes about the audit range, for example aggregate trust score, conflict count, or epistemic tag breakdown. Receivers MUST ignore fields they do not recognize. |
| `revocation` | object | Optional revocation hint (`{ "url": "..." }`). See section 9. |

## 4. Verifiable Credentials profile

A Memory Receipt MAY be wrapped as a VC by adding a `@context`, a `type` array containing `"VerifiableCredential"` and `"ChrysalisMemoryReceipt"`, and a `credentialSubject` block that mirrors `subject` and `audit_log_range`. The plain-JSON form remains canonical for v0.1 interoperability testing.

## 5. Identity hash

`subject.identity_hash` is computed as:

```
identity_hash = "0x" + sha256(canonical_identity_payload)
```

where `canonical_identity_payload` is a UTF-8 byte string consisting of:

```
agent_id || "\0" || issuer || "\0" || subject_public_key_pem
```

The subject public key is the agent's signing key, if any. When the agent has no signing key, `subject_public_key_pem` is the empty string.

## 6. Audit log canonical encoding

Each audit record contributing to `merkle_root` is canonicalized as a JSON object containing exactly the following fields, in this order:

```json
{
  "entry_id": "...",
  "session_id": "...",
  "key": "...",
  "content_hash": "0x...",
  "epistemic_tag": "...",
  "critique_verdict": "...",
  "recorded_at": "..."
}
```

`content_hash` is the SHA-256 of the UTF-8 belief content. The canonical form is the UTF-8 JSON encoding produced with sorted keys, no whitespace, and no trailing newline.

The Merkle tree is a binary tree of SHA-256 hashes. Odd levels duplicate the last leaf. Leaves are the SHA-256 of the canonical record encoding. The Merkle root is encoded as `"0x" + hex(root)`.

## 7. Anchors

`anchors` is a non-empty array of objects, each:

| Field | Type | Description |
|---|---|---|
| `chain` | string | Anchor chain identifier. Reserved values for v0.1: `"solana"`, `"base"`, `"local"`. |
| `tx` | string | Transaction identifier on the chain. |
| `block_number` | integer (optional) | Block height of inclusion. Strongly recommended. |
| `verifier_url` | string (optional) | Block explorer URL that surfaces the transaction for visual inspection. |

A receipt with `chain = "local"` is intended for development only. Production receivers SHOULD require at least one anchor with a chain other than `"local"`.

## 8. Signature

The signature is computed over the canonical payload, which is the receipt object with `signature` removed and serialized as UTF-8 JSON with sorted keys and no whitespace.

v0.1 supports two signature schemes, selected by the issuer DID method:

| DID method | Algorithm | Encoding |
|---|---|---|
| `did:chrysalis:<slug>` | Ed25519 | 64-byte signature, hex with `0x` prefix |
| `did:key:<multibase>` | as encoded in the DID | as encoded in the DID |

Future versions may add additional schemes. Receivers MUST reject signatures using an algorithm they do not support.

## 9. Revocation

A receipt MAY include an optional `revocation` block:

```json
{ "revocation": { "url": "https://issuer.example/revocations/{receipt_hash}" } }
```

The URL, when fetched, returns either a 404 (not revoked) or a 200 with a JSON object containing `revoked_at`. Receivers SHOULD treat any 2xx response as revocation.

## 10. Verification procedure

A receiver verifies a Memory Receipt by performing every step below. A failure at any step is a verification failure.

1. Parse the receipt as JSON. Reject if any required field from section 3 is missing.
2. Confirm `version == "1"`.
3. Confirm `issued_at` is in the past and `expires_at` is in the future.
4. Resolve the issuer DID to a public key. For `did:chrysalis:<slug>`, the public key is published at `https://chrysalis.dev/.well-known/issuers/<slug>.json`.
5. Reconstruct the canonical payload (the receipt with `signature` removed, serialized with sorted keys and no whitespace).
6. Verify the signature over the canonical payload against the issuer public key.
7. For at least one anchor with `chain != "local"`, fetch the transaction and confirm it commits the same `merkle_root` reported by the receipt.
8. If `revocation.url` is present, perform the revocation check and reject revoked receipts.
9. Optionally: when an audit log range is available to the receiver, recompute the Merkle root and confirm it matches.

## 11. Privacy

Memory Receipts deliberately do not include belief content. The Merkle root commits to a set of beliefs without revealing them. Receivers that need belief content can request it directly from the issuer or from the agent under whatever terms apply to the data. The receipt only proves that a specific set existed and was anchored.

## 12. Open questions for v1.0

- Selective disclosure of individual beliefs via Merkle proof packaging.
- Standard set of `attributes` field names (trust score, conflict count, ...).
- Cross-issuer chains of trust (issuer A vouches for issuer B).
- Registry of DID methods accepted at v1.0.
- Receipt versioning and migration when the canonical encoding changes.

Comments welcome at hello@imagolabs.dev.
