# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
"""Generates the example receipt files in this directory.

Run with: python examples/generate_examples.py
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from chrysalis_receipts import canonical_payload, compute_merkle_root

HERE = Path(__file__).parent


def _records():
    return [
        {
            "entry_id": "e_1",
            "session_id": "s_demo",
            "key": "user_pref",
            "content": "User prefers brevity.",
            "epistemic_tag": "ASSUMED",
            "critique_verdict": "PASS",
            "recorded_at": "2026-05-01T12:00:00Z",
        },
        {
            "entry_id": "e_2",
            "session_id": "s_demo",
            "key": "user_pref2",
            "content": "User likes diagrams.",
            "epistemic_tag": "VERIFIED",
            "critique_verdict": "PASS",
            "recorded_at": "2026-05-01T12:05:00Z",
        },
    ]


def _build_receipt(priv: Ed25519PrivateKey, *, expires_at: datetime) -> dict:
    issued_at = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    receipt = {
        "version": "1",
        "issuer": "did:chrysalis:imago-labs",
        "subject": {
            "agent_id": "agent_demo",
            "identity_hash": "0x" + ("a" * 64),
        },
        "audit_log_range": {
            "merkle_root": compute_merkle_root(_records()),
            "from_index": 0,
            "to_index": 1,
        },
        "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "anchors": [{"chain": "local", "tx": "local-0"}],
    }
    payload = canonical_payload(receipt)
    receipt["signature"] = "0x" + priv.sign(payload).hex()
    return receipt


def main() -> None:
    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        encoding=Encoding.Raw, format=PublicFormat.Raw
    ).hex()

    valid_dev = _build_receipt(
        priv, expires_at=datetime(2027, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    )
    expired = _build_receipt(
        priv,
        expires_at=datetime(2026, 5, 1, 13, 0, 0, tzinfo=timezone.utc),
    )
    tampered = _build_receipt(
        priv, expires_at=datetime(2027, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    )
    # Tamper after signing so the signature no longer matches.
    tampered["subject"]["agent_id"] = "agent_evil"

    (HERE / "valid_dev_receipt.json").write_text(
        json.dumps(valid_dev, indent=2) + "\n"
    )
    (HERE / "expired_receipt.json").write_text(
        json.dumps(expired, indent=2) + "\n"
    )
    (HERE / "tampered_receipt.json").write_text(
        json.dumps(tampered, indent=2) + "\n"
    )
    (HERE / "keys.json").write_text(
        json.dumps(
            {
                "note": (
                    "Test-only keypair used to sign the example receipts. "
                    "Do not reuse outside verifier tests."
                ),
                "public_key_hex": pub_hex,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
