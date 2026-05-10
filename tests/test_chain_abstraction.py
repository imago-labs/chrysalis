# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at the root of this repository
# or at http://www.apache.org/licenses/LICENSE-2.0
"""
Chain Abstraction Layer Tests
------------------------------
Tests for the provider interface, DryRunProvider, SolanaProvider wrapper,
and factory function.
"""

from __future__ import annotations

import asyncio

import pytest

from memoir.chain.provider import (
    AttestationProvider,
    AttestationRecord,
    CostEstimate,
    TransactionReceipt,
)
from memoir.chain.dry_run_provider import DryRunProvider
from memoir.chain.factory import create_provider


# ---------------------------------------------------------------------------
# Helper to run async in sync tests
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# DryRunProvider Tests
# ---------------------------------------------------------------------------

class TestDryRunProvider:

    def test_implements_interface(self):
        provider = DryRunProvider()
        assert isinstance(provider, AttestationProvider)

    def test_attest_returns_receipt(self):
        provider = DryRunProvider()
        receipt = run(provider.attest(
            entry_id="e1", session_id="s1", key="k1",
            epistemic_tag="VERIFIED", critique_verdict="PASS",
            audit_id="a1", approved=True,
        ))
        assert isinstance(receipt, TransactionReceipt)
        assert receipt.tx_signature.startswith("DRY_")
        assert receipt.provider == "dry_run"
        assert receipt.approved is True

    def test_attest_deterministic(self):
        provider = DryRunProvider()
        r1 = run(provider.attest("e1", "s1", "k1", "VERIFIED", "PASS", "a1", True))
        r2 = run(provider.attest("e1", "s1", "k1", "VERIFIED", "PASS", "a1", True))
        assert r1.content_hash == r2.content_hash

    def test_verify_after_attest(self):
        provider = DryRunProvider()
        receipt = run(provider.attest("e1", "s1", "k1", "VERIFIED", "PASS", "a1", True))
        verification = run(provider.verify(receipt.tx_signature))
        assert verification.verified is True
        assert verification.content_hash == receipt.content_hash

    def test_verify_unknown_tx(self):
        provider = DryRunProvider()
        verification = run(provider.verify("nonexistent-tx"))
        assert verification.verified is False

    def test_session_history(self):
        provider = DryRunProvider()
        run(provider.attest("e1", "s1", "k1", "VERIFIED", "PASS", "a1", True))
        run(provider.attest("e2", "s1", "k2", "INFERRED", "FLAG", "a2", True))
        run(provider.attest("e3", "s2", "k3", "ASSUMED", "PASS", "a3", True))

        history = run(provider.get_session_history("s1"))
        assert len(history) == 2

        history_s2 = run(provider.get_session_history("s2"))
        assert len(history_s2) == 1

    def test_estimate_cost_free(self):
        provider = DryRunProvider()
        cost = run(provider.estimate_cost(100))
        assert cost.estimated_cost_sol == 0.0
        assert cost.estimated_cost_usd == 0.0

    def test_empty_session_history(self):
        provider = DryRunProvider()
        history = run(provider.get_session_history("nonexistent"))
        assert history == []


# ---------------------------------------------------------------------------
# Factory Tests
# ---------------------------------------------------------------------------

class TestFactory:

    def test_default_is_dry_run(self):
        provider = create_provider()
        assert isinstance(provider, DryRunProvider)

    def test_explicit_dry_run(self):
        provider = create_provider(provider_type="dry_run")
        assert isinstance(provider, DryRunProvider)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown attestation provider"):
            create_provider(provider_type="ethereum")

    def test_factory_creates_working_provider(self):
        provider = create_provider(provider_type="dry_run")
        receipt = run(provider.attest("e1", "s1", "k1", "VERIFIED", "PASS", "a1", True))
        assert receipt.tx_signature.startswith("DRY_")
