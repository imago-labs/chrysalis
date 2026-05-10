# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
"""
chrysalis-receipts

Memory Receipts open standard: spec, reference verifier, and examples.
See spec/SCHEMA.md for the v0.1 specification.
"""

from chrysalis_receipts.verifier import (
    VerificationError,
    canonical_payload,
    canonical_record,
    compute_merkle_root,
    verify_receipt,
)

__all__ = [
    "VerificationError",
    "canonical_payload",
    "canonical_record",
    "compute_merkle_root",
    "verify_receipt",
]

__version__ = "0.1.0"
