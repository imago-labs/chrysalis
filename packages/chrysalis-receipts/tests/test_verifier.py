# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
"""Tests for the Memory Receipts v0.1 reference verifier."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from chrysalis_receipts import (
    VerificationError,
    canonical_payload,
    compute_merkle_root,
    verify_receipt,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_keypair() -> tuple[Ed25519PrivateKey, bytes]:
    priv = Ed25519PrivateKey.generate()
    pub_bytes = priv.public_key().public_bytes(
        encoding=Encoding.Raw, format=PublicFormat.Raw
    )
    return priv, pub_bytes


def _sample_records() -> list[dict]:
    return [
        {
            "entry_id": "e_1",
            "session_id": "s_1",
            "key": "user_pref",
            "content": "User prefers brevity.",
            "epistemic_tag": "ASSUMED",
            "critique_verdict": "PASS",
            "recorded_at": "2026-05-01T12:00:00Z",
        },
        {
            "entry_id": "e_2",
            "session_id": "s_1",
            "key": "user_pref2",
            "content": "User likes diagrams.",
            "epistemic_tag": "VERIFIED",
            "critique_verdict": "PASS",
            "recorded_at": "2026-05-01T12:05:00Z",
        },
    ]


def _build_receipt(priv: Ed25519PrivateKey, *, expires_in: timedelta = timedelta(days=30)) -> dict:
    records = _sample_records()
    merkle_root = compute_merkle_root(records)
    issued_at = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    expires_at = issued_at + expires_in
    receipt = {
        "version": "1",
        "issuer": "did:chrysalis:imago-labs",
        "subject": {
            "agent_id": "agent_abc",
            "identity_hash": "0x" + ("a" * 64),
        },
        "audit_log_range": {
            "merkle_root": merkle_root,
            "from_index": 0,
            "to_index": 1,
        },
        "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "anchors": [
            {"chain": "solana", "tx": "5abc...", "block_number": 12345},
        ],
    }
    payload = canonical_payload(receipt)
    signature = priv.sign(payload)
    receipt["signature"] = "0x" + signature.hex()
    return receipt


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_valid_receipt_verifies() -> None:
    priv, pub = _make_keypair()
    receipt = _build_receipt(priv)

    result = verify_receipt(
        receipt,
        issuer_key_resolver=lambda issuer: pub,
        anchor_checker=lambda anchor, root: anchor["chain"] == "solana"
        and root == receipt["audit_log_range"]["merkle_root"],
        now=datetime(2026, 5, 2, tzinfo=timezone.utc),
    )

    assert result.ok is True
    assert "signature" in result.checks_passed
    assert "anchor" in result.checks_passed


def test_tampered_receipt_fails_signature_check() -> None:
    priv, pub = _make_keypair()
    receipt = _build_receipt(priv)

    # Change a field after signing.
    receipt["subject"]["agent_id"] = "agent_evil"

    with pytest.raises(VerificationError, match="signature did not verify"):
        verify_receipt(
            receipt,
            issuer_key_resolver=lambda issuer: pub,
            anchor_checker=lambda *_: True,
            now=datetime(2026, 5, 2, tzinfo=timezone.utc),
        )


def test_expired_receipt_fails() -> None:
    priv, pub = _make_keypair()
    receipt = _build_receipt(priv, expires_in=timedelta(hours=1))

    with pytest.raises(VerificationError, match="expired"):
        verify_receipt(
            receipt,
            issuer_key_resolver=lambda issuer: pub,
            anchor_checker=lambda *_: True,
            now=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )


def test_missing_required_field_fails() -> None:
    priv, pub = _make_keypair()
    receipt = _build_receipt(priv)
    del receipt["audit_log_range"]

    with pytest.raises(VerificationError, match="missing required fields"):
        verify_receipt(
            receipt,
            issuer_key_resolver=lambda issuer: pub,
            now=datetime(2026, 5, 2, tzinfo=timezone.utc),
        )


def test_unsupported_issuer_method_rejected() -> None:
    priv, pub = _make_keypair()
    receipt = _build_receipt(priv)
    receipt["issuer"] = "did:web:example.com"
    payload = canonical_payload(receipt)
    receipt["signature"] = "0x" + priv.sign(payload).hex()

    with pytest.raises(VerificationError, match="did:chrysalis"):
        verify_receipt(
            receipt,
            issuer_key_resolver=lambda issuer: pub,
            now=datetime(2026, 5, 2, tzinfo=timezone.utc),
        )


def test_local_only_receipt_warns_but_passes() -> None:
    priv, pub = _make_keypair()
    receipt = _build_receipt(priv)
    receipt["anchors"] = [{"chain": "local", "tx": "local-0"}]
    receipt["signature"] = "0x" + priv.sign(canonical_payload(receipt)).hex()

    result = verify_receipt(
        receipt,
        issuer_key_resolver=lambda issuer: pub,
        anchor_checker=lambda *_: True,
        now=datetime(2026, 5, 2, tzinfo=timezone.utc),
    )
    assert result.ok is True
    assert any("dev-only" in w for w in result.warnings)


def test_merkle_root_recomputation_catches_mismatch() -> None:
    priv, pub = _make_keypair()
    receipt = _build_receipt(priv)
    bad_records = _sample_records()
    bad_records[0]["content"] = "User actually prefers verbosity."

    with pytest.raises(VerificationError, match="merkle_root mismatch"):
        verify_receipt(
            receipt,
            issuer_key_resolver=lambda issuer: pub,
            anchor_checker=lambda *_: True,
            now=datetime(2026, 5, 2, tzinfo=timezone.utc),
            expected_records=bad_records,
        )


def test_canonical_payload_is_stable() -> None:
    receipt = {
        "z": 1,
        "a": 2,
        "signature": "0xff",
    }
    payload = canonical_payload(receipt)
    # Sorted keys, no whitespace, no signature.
    assert payload == b'{"a":2,"z":1}'
    # Round trip parseable.
    json.loads(payload)
