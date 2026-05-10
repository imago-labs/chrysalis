"""
MEMOIR Chain — Attestation Module Tests
----------------------------------------
Tests hash computation, PDA derivation, instruction serialization,
and dry-run attestation. No live Solana network required.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey

import sys

from memoir.chain.attester import (
    SolanaAttester,
    AttestationParams,
    compute_content_hash,
    compute_key_hash,
    derive_session_pda,
    build_attest_instruction,
    build_initialize_session_instruction,
    MEMOIR_PROGRAM_ID,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def keypair():
    return Keypair()


@pytest.fixture
def program_id():
    # Use system program as a valid placeholder for testing
    return Pubkey.from_string("11111111111111111111111111111111")


@pytest.fixture
def attester(keypair, tmp_path):
    """DRY RUN attester — no network calls."""
    kp_path = tmp_path / "id.json"
    kp_path.write_text(json.dumps(list(bytes(keypair))))
    return SolanaAttester(
        keypair=keypair,
        cluster="devnet",
        program_id=str(Pubkey.from_string("11111111111111111111111111111111")),
        dry_run=True,
    )


# ---------------------------------------------------------------------------
# Hash utility tests
# ---------------------------------------------------------------------------

class TestHashUtils:

    def test_content_hash_is_32_bytes(self):
        h = compute_content_hash(
            entry_id="entry-123",
            session_id="session-abc",
            key="auth_function",
            epistemic_tag="VERIFIED",
            critique_verdict="PASS",
            audit_id="audit-xyz",
        )
        assert isinstance(h, bytes)
        assert len(h) == 32

    def test_content_hash_is_deterministic(self):
        args = dict(
            entry_id="e1",
            session_id="s1",
            key="k1",
            epistemic_tag="ASSUMED",
            critique_verdict="FLAG",
            audit_id="a1",
        )
        assert compute_content_hash(**args) == compute_content_hash(**args)

    def test_content_hash_changes_with_tag(self):
        base = dict(entry_id="e", session_id="s", key="k", audit_id="a")
        h1 = compute_content_hash(**base, epistemic_tag="VERIFIED", critique_verdict="PASS")
        h2 = compute_content_hash(**base, epistemic_tag="ASSUMED", critique_verdict="PASS")
        assert h1 != h2

    def test_key_hash_is_32_bytes(self):
        h = compute_key_hash("auth_implementation")
        assert len(h) == 32

    def test_key_hash_is_deterministic(self):
        assert compute_key_hash("mykey") == compute_key_hash("mykey")

    def test_different_keys_produce_different_hashes(self):
        assert compute_key_hash("key_a") != compute_key_hash("key_b")

    def test_content_hash_uses_all_fields(self):
        """Changing any field should change the hash."""
        base = dict(
            entry_id="e", session_id="s", key="k",
            epistemic_tag="VERIFIED", critique_verdict="PASS", audit_id="a"
        )
        original = compute_content_hash(**base)

        for field, new_val in [
            ("entry_id", "DIFFERENT"),
            ("session_id", "DIFFERENT"),
            ("key", "DIFFERENT"),
            ("epistemic_tag", "ASSUMED"),
            ("critique_verdict", "REJECT"),
            ("audit_id", "DIFFERENT"),
        ]:
            modified = {**base, field: new_val}
            assert compute_content_hash(**modified) != original, \
                f"Hash should change when {field} changes"


# ---------------------------------------------------------------------------
# PDA derivation tests
# ---------------------------------------------------------------------------

class TestPDADerivation:

    def test_pda_is_deterministic(self, program_id):
        pda1, bump1 = derive_session_pda("session-abc", program_id)
        pda2, bump2 = derive_session_pda("session-abc", program_id)
        assert pda1 == pda2
        assert bump1 == bump2

    def test_different_sessions_produce_different_pdas(self, program_id):
        pda1, _ = derive_session_pda("session-001", program_id)
        pda2, _ = derive_session_pda("session-002", program_id)
        assert pda1 != pda2

    def test_pda_is_off_curve(self, program_id):
        """PDAs must be off the Ed25519 curve (not valid signing keys)."""
        pda, _ = derive_session_pda("test-session", program_id)
        assert not pda.is_on_curve()


# ---------------------------------------------------------------------------
# Instruction builder tests
# ---------------------------------------------------------------------------

class TestInstructionBuilders:

    def _make_params(self):
        return AttestationParams(
            entry_id="entry-uuid-001",
            session_id="session-001",
            key_hash=hashlib.sha256(b"auth_key").digest(),
            content_hash=hashlib.sha256(b"content").digest(),
            epistemic_tag="VERIFIED",
            critique_verdict="PASS",
            approved=True,
        )

    def test_attest_instruction_has_correct_discriminator(self, keypair, program_id):
        session_pda, _ = derive_session_pda("session-001", program_id)
        params = self._make_params()

        ix = build_attest_instruction(
            program_id=program_id,
            attester=keypair.pubkey(),
            session_pda=session_pda,
            params=params,
        )

        expected_discriminator = hashlib.sha256(b"global:attest").digest()[:8]
        assert ix.data[:8] == expected_discriminator

    def test_attest_instruction_has_three_accounts(self, keypair, program_id):
        session_pda, _ = derive_session_pda("s1", program_id)
        ix = build_attest_instruction(
            program_id=program_id,
            attester=keypair.pubkey(),
            session_pda=session_pda,
            params=self._make_params(),
        )
        assert len(ix.accounts) == 3

    def test_attester_is_signer(self, keypair, program_id):
        session_pda, _ = derive_session_pda("s1", program_id)
        ix = build_attest_instruction(
            program_id=program_id,
            attester=keypair.pubkey(),
            session_pda=session_pda,
            params=self._make_params(),
        )
        assert ix.accounts[0].is_signer is True
        assert ix.accounts[0].pubkey == keypair.pubkey()

    def test_init_session_instruction_discriminator(self, keypair, program_id):
        session_pda, _ = derive_session_pda("s1", program_id)
        ix = build_initialize_session_instruction(
            program_id=program_id,
            attester=keypair.pubkey(),
            session_pda=session_pda,
            session_id="session-001",
        )
        expected = hashlib.sha256(b"global:initialize_session").digest()[:8]
        assert ix.data[:8] == expected

    def test_serialized_params_contain_content_hash(self, keypair, program_id):
        params = self._make_params()
        session_pda, _ = derive_session_pda("s1", program_id)
        ix = build_attest_instruction(
            program_id=program_id,
            attester=keypair.pubkey(),
            session_pda=session_pda,
            params=params,
        )
        # content_hash should appear in the serialized data (after discriminator)
        assert params.content_hash in ix.data[8:]

    def test_approved_true_serialized_as_0x01(self, keypair, program_id):
        params = self._make_params()
        params.approved = True
        session_pda, _ = derive_session_pda("s1", program_id)
        ix = build_attest_instruction(program_id, keypair.pubkey(), session_pda, params)
        assert b"\x01" in ix.data[8:]

    def test_approved_false_serialized_as_0x00(self, keypair, program_id):
        params = self._make_params()
        params.approved = False
        session_pda, _ = derive_session_pda("s1", program_id)
        ix = build_attest_instruction(program_id, keypair.pubkey(), session_pda, params)
        assert b"\x00" in ix.data[8:]


# ---------------------------------------------------------------------------
# SolanaAttester dry-run tests
# ---------------------------------------------------------------------------

class TestSolanaAttesterDryRun:

    def test_dry_run_returns_result(self, attester):
        result = asyncio.get_event_loop().run_until_complete(
            attester.attest(
                entry_id="entry-001",
                session_id="session-001",
                key="auth_function",
                epistemic_tag="VERIFIED",
                critique_verdict="PASS",
                audit_id="audit-001",
                approved=True,
            )
        )
        assert result is not None
        assert result.approved is True
        assert result.epistemic_tag == "VERIFIED"
        assert result.critique_verdict == "PASS"

    def test_dry_run_signature_starts_with_DRY(self, attester):
        result = asyncio.get_event_loop().run_until_complete(
            attester.attest(
                entry_id="e", session_id="s", key="k",
                epistemic_tag="ASSUMED", critique_verdict="FLAG",
                audit_id="a", approved=True,
            )
        )
        assert result.tx_signature.startswith("DRY")

    def test_dry_run_has_explorer_url(self, attester):
        result = asyncio.get_event_loop().run_until_complete(
            attester.attest(
                entry_id="e", session_id="s", key="k",
                epistemic_tag="INFERRED", critique_verdict="PASS",
                audit_id="a", approved=True,
            )
        )
        assert "explorer.solana.com" in result.explorer_url

    def test_dry_run_content_hash_is_hex(self, attester):
        result = asyncio.get_event_loop().run_until_complete(
            attester.attest(
                entry_id="e", session_id="s", key="k",
                epistemic_tag="VERIFIED", critique_verdict="PASS",
                audit_id="a", approved=True,
            )
        )
        # Should be 64 hex chars (32 bytes)
        assert len(result.content_hash) == 64
        int(result.content_hash, 16)  # should not raise

    def test_dry_run_latency_recorded(self, attester):
        result = asyncio.get_event_loop().run_until_complete(
            attester.attest(
                entry_id="e", session_id="s", key="k",
                epistemic_tag="ASSUMED", critique_verdict="REJECT",
                audit_id="a", approved=False,
            )
        )
        assert result.latency_ms >= 0

    def test_rejected_entry_approved_is_false(self, attester):
        result = asyncio.get_event_loop().run_until_complete(
            attester.attest(
                entry_id="e", session_id="s", key="k",
                epistemic_tag="REJECTED", critique_verdict="REJECT",
                audit_id="a", approved=False,
            )
        )
        assert result.approved is False

    def test_same_inputs_produce_same_hash(self, attester):
        kwargs = dict(
            entry_id="e", session_id="s", key="k",
            epistemic_tag="VERIFIED", critique_verdict="PASS",
            audit_id="a", approved=True,
        )
        r1 = asyncio.get_event_loop().run_until_complete(attester.attest(**kwargs))
        r2 = asyncio.get_event_loop().run_until_complete(attester.attest(**kwargs))
        assert r1.content_hash == r2.content_hash

    def test_keypair_generation_on_missing_file(self, tmp_path):
        """Should auto-generate a keypair if the file doesn't exist."""
        kp_path = tmp_path / "nonexistent" / "id.json"
        a = SolanaAttester(
            keypair=SolanaAttester._load_keypair(str(kp_path)),
            cluster="devnet",
            program_id="11111111111111111111111111111111",
            dry_run=True,
        )
        assert a is not None
        assert kp_path.exists()
