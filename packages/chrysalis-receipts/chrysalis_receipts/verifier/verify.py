# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
"""
Reference verifier for Memory Receipts v0.1.

This module implements the structural and cryptographic checks defined in
spec/SCHEMA.md. It does not perform network calls: on-chain anchor checks
and DID resolution are left to callers, who pass in a fetcher and an
issuer key resolver. That keeps the reference implementation easy to test
and easy to embed in environments without internet access.

Signature scheme supported at v0.1: Ed25519 over the canonical payload.
Other schemes raise VerificationError.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable


class VerificationError(Exception):
    """Raised when a receipt fails any structural or cryptographic check."""


# ---------------------------------------------------------------------------
# Canonical encoding helpers
# ---------------------------------------------------------------------------


def canonical_payload(receipt: dict) -> bytes:
    """Return the canonical bytes signed by the issuer.

    The canonical payload is the receipt with `signature` removed, then
    serialized as UTF-8 JSON with sorted keys, no whitespace, and no
    trailing newline.
    """
    payload = {k: v for k, v in receipt.items() if k != "signature"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_record(record: dict) -> bytes:
    """Canonical encoding of a single audit log record.

    Exactly the fields listed in section 6 are included, in the order
    sorted-keys produces, and the belief content is hashed before
    encoding so the record encoding never carries raw belief text.
    """
    if "content" in record and "content_hash" not in record:
        content_hash = "0x" + hashlib.sha256(
            record["content"].encode("utf-8")
        ).hexdigest()
    else:
        content_hash = record.get("content_hash", "")

    canonical = {
        "entry_id": record["entry_id"],
        "session_id": record["session_id"],
        "key": record["key"],
        "content_hash": content_hash,
        "epistemic_tag": record["epistemic_tag"],
        "critique_verdict": record["critique_verdict"],
        "recorded_at": record["recorded_at"],
    }
    return json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_merkle_root(records: Iterable[dict]) -> str:
    """Compute the Merkle root over a sequence of audit records.

    Binary tree of SHA-256 hashes. Odd levels duplicate the last leaf.
    Returns the root as a 0x-prefixed hex string.
    """
    leaves = [hashlib.sha256(canonical_record(r)).digest() for r in records]
    if not leaves:
        raise VerificationError("cannot compute Merkle root over empty record set")

    layer = leaves
    while len(layer) > 1:
        if len(layer) % 2 == 1:
            layer = layer + [layer[-1]]
        layer = [
            hashlib.sha256(layer[i] + layer[i + 1]).digest()
            for i in range(0, len(layer), 2)
        ]
    return "0x" + layer[0].hex()


# ---------------------------------------------------------------------------
# Verification entry point
# ---------------------------------------------------------------------------


_REQUIRED_TOP = {
    "version",
    "issuer",
    "subject",
    "audit_log_range",
    "issued_at",
    "expires_at",
    "anchors",
    "signature",
}
_REQUIRED_SUBJECT = {"agent_id", "identity_hash"}
_REQUIRED_RANGE = {"merkle_root", "from_index", "to_index"}


@dataclass
class VerificationResult:
    """Outcome of verify_receipt. ok=True only when every required check passed."""

    ok: bool
    checks_passed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _parse_rfc3339(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


IssuerKeyResolver = Callable[[str], bytes]
AnchorChecker = Callable[[dict, str], bool]


def verify_receipt(
    receipt: dict,
    *,
    issuer_key_resolver: IssuerKeyResolver,
    anchor_checker: AnchorChecker | None = None,
    now: datetime | None = None,
    expected_records: list[dict] | None = None,
) -> VerificationResult:
    """Verify a Memory Receipt against the v0.1 spec.

    Args:
        receipt: Parsed receipt dict.
        issuer_key_resolver: Callable that maps an issuer DID to a raw
            Ed25519 public key (32 bytes). Callers wire this to a real DID
            resolver, a static dict in tests, or a local keystore.
        anchor_checker: Optional callable invoked once per non-local anchor.
            Receives (anchor_dict, expected_merkle_root_hex) and returns
            True if the on-chain transaction commits the expected root. When
            None, anchor checks are skipped and a warning is recorded.
        now: Time to evaluate `issued_at` and `expires_at` against. Defaults
            to datetime.now(timezone.utc).
        expected_records: If provided, the verifier recomputes the Merkle
            root over these records and asserts it matches the receipt's
            `audit_log_range.merkle_root`.
    """
    result = VerificationResult(ok=False)

    # -- Structural checks ----------------------------------------------------
    missing = _REQUIRED_TOP - receipt.keys()
    if missing:
        raise VerificationError(f"missing required fields: {sorted(missing)}")

    if receipt["version"] != "1":
        raise VerificationError(f"unsupported version: {receipt['version']!r}")
    result.checks_passed.append("version")

    subject = receipt["subject"]
    if not isinstance(subject, dict) or (_REQUIRED_SUBJECT - subject.keys()):
        raise VerificationError("subject must include agent_id and identity_hash")
    result.checks_passed.append("subject_shape")

    log_range = receipt["audit_log_range"]
    if not isinstance(log_range, dict) or (_REQUIRED_RANGE - log_range.keys()):
        raise VerificationError(
            "audit_log_range must include merkle_root, from_index, to_index"
        )
    if log_range["from_index"] > log_range["to_index"]:
        raise VerificationError("from_index > to_index")
    result.checks_passed.append("audit_log_range_shape")

    anchors = receipt["anchors"]
    if not isinstance(anchors, list) or not anchors:
        raise VerificationError("anchors must be a non-empty array")
    result.checks_passed.append("anchors_shape")

    # -- Time window ----------------------------------------------------------
    now = now or datetime.now(timezone.utc)
    issued_at = _parse_rfc3339(receipt["issued_at"])
    expires_at = _parse_rfc3339(receipt["expires_at"])
    if issued_at > now:
        raise VerificationError(f"issued_at is in the future: {receipt['issued_at']}")
    if expires_at <= now:
        raise VerificationError(f"receipt expired at {receipt['expires_at']}")
    result.checks_passed.append("time_window")

    # -- Signature ------------------------------------------------------------
    issuer = receipt["issuer"]
    if not issuer.startswith("did:chrysalis:"):
        raise VerificationError(
            f"v0.1 reference verifier only supports did:chrysalis issuers, got {issuer!r}"
        )

    sig_hex = receipt["signature"]
    if not isinstance(sig_hex, str) or not sig_hex.startswith("0x"):
        raise VerificationError("signature must be a 0x-prefixed hex string")
    try:
        signature = bytes.fromhex(sig_hex[2:])
    except ValueError as e:
        raise VerificationError(f"invalid signature encoding: {e}") from e
    if len(signature) != 64:
        raise VerificationError(
            f"Ed25519 signature must be 64 bytes, got {len(signature)}"
        )

    public_key = issuer_key_resolver(issuer)
    if not isinstance(public_key, (bytes, bytearray)) or len(public_key) != 32:
        raise VerificationError("issuer_key_resolver must return 32 raw Ed25519 bytes")

    payload = canonical_payload(receipt)
    _verify_ed25519(public_key, signature, payload)
    result.checks_passed.append("signature")

    # -- Anchor check ---------------------------------------------------------
    non_local = [a for a in anchors if a.get("chain") != "local"]
    if not non_local:
        result.warnings.append("only local anchors present; receipt is dev-only")
    elif anchor_checker is None:
        result.warnings.append("no anchor_checker supplied; chain anchor not verified")
    else:
        merkle_root = log_range["merkle_root"]
        any_ok = any(anchor_checker(a, merkle_root) for a in non_local)
        if not any_ok:
            raise VerificationError(
                "no anchor checker confirmed the merkle_root on chain"
            )
        result.checks_passed.append("anchor")

    # -- Optional Merkle recomputation ---------------------------------------
    if expected_records is not None:
        recomputed = compute_merkle_root(expected_records)
        if recomputed != log_range["merkle_root"]:
            raise VerificationError(
                f"merkle_root mismatch: receipt={log_range['merkle_root']} "
                f"recomputed={recomputed}"
            )
        result.checks_passed.append("merkle_root_recomputed")

    result.ok = True
    return result


# ---------------------------------------------------------------------------
# Ed25519 verification
# ---------------------------------------------------------------------------


def _verify_ed25519(public_key: bytes, signature: bytes, payload: bytes) -> None:
    """Verify an Ed25519 signature, raising VerificationError on mismatch.

    Uses cryptography if available, otherwise falls back to PyNaCl. If
    neither is installed, raises VerificationError so the caller can
    surface a clean install hint.
    """
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )

        try:
            Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)
            return
        except InvalidSignature as e:
            raise VerificationError("Ed25519 signature did not verify") from e
    except ImportError:
        pass

    try:
        import nacl.signing

        try:
            nacl.signing.VerifyKey(public_key).verify(payload, signature)
            return
        except Exception as e:
            raise VerificationError(f"Ed25519 signature did not verify: {e}") from e
    except ImportError as e:
        raise VerificationError(
            "Ed25519 verification needs either cryptography or pynacl installed."
        ) from e
