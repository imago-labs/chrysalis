"""
MEMOIR Test Suite
-----------------
Tests for the epistemic classifier, audit logger, and pipeline.
Critique agent tests are mocked to avoid API calls in CI.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from memoir.core.audit import AuditLogger
from memoir.core.classifier import EpistemicClassifier
from memoir.core.pipeline import MEMOIRPipeline
from memoir.models.memory import (
    AuditRecord,
    CritiqueVerdict,
    EpistemicTag,
    MemoryEntry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test_audit.db")


@pytest.fixture
def tmp_workspace(tmp_path):
    # Create a sample source file for verifier tests
    src = tmp_path / "main.py"
    src.write_text("def authenticate(user, password):\n    return check_db(user, password)\n")
    return str(tmp_path)


@pytest.fixture
def sample_entry():
    return MemoryEntry(
        session_id="test-session-001",
        key="auth_implementation",
        content="Authentication is handled by the authenticate() function in main.py",
        source_reference="main.py",
    )


@pytest.fixture
def classifier():
    return EpistemicClassifier()


# ---------------------------------------------------------------------------
# Epistemic Classifier Tests
# ---------------------------------------------------------------------------

class TestEpistemicClassifier:

    def test_file_reference_classifies_verified(self, classifier):
        entry = MemoryEntry(
            session_id="s1", key="k1",
            content="The function is defined here.",
            source_reference="src/main.py",
        )
        tag = classifier.classify(entry)
        assert tag == EpistemicTag.VERIFIED

    def test_url_reference_classifies_verified(self, classifier):
        entry = MemoryEntry(
            session_id="s1", key="k1",
            content="API endpoint documented here.",
            source_reference="https://api.example.com/docs",
        )
        tag = classifier.classify(entry)
        assert tag == EpistemicTag.VERIFIED

    def test_inference_language_classifies_inferred(self, classifier):
        entry = MemoryEntry(
            session_id="s1", key="k1",
            content="Based on the auth module, it follows that tokens expire after 24h.",
        )
        tag = classifier.classify(entry)
        assert tag == EpistemicTag.INFERRED

    def test_assumption_language_classifies_assumed(self, classifier):
        entry = MemoryEntry(
            session_id="s1", key="k1",
            content="The database should be PostgreSQL by default.",
        )
        tag = classifier.classify(entry)
        assert tag == EpistemicTag.ASSUMED

    def test_no_source_no_signals_classifies_assumed(self, classifier):
        entry = MemoryEntry(
            session_id="s1", key="k1",
            content="There are three microservices in this system.",
        )
        tag = classifier.classify(entry)
        assert tag == EpistemicTag.ASSUMED

    def test_stale_detection_on_expired_verified(self, classifier):
        entry = MemoryEntry(
            session_id="s1", key="k1",
            content="Something verified.",
            source_reference="main.py",
        )
        # Simulate a verified_at 48 hours ago
        old_verified = datetime.now(timezone.utc) - timedelta(hours=48)
        tag = classifier.classify(entry, existing_verified_at=old_verified)
        assert tag == EpistemicTag.STALE

    def test_fresh_verified_not_stale(self, classifier):
        entry = MemoryEntry(
            session_id="s1", key="k1",
            content="Something verified.",
            source_reference="main.py",
        )
        recent_verified = datetime.now(timezone.utc) - timedelta(hours=1)
        tag = classifier.classify(entry, existing_verified_at=recent_verified)
        assert tag == EpistemicTag.VERIFIED

    def test_stale_after_computed_for_verified(self, classifier):
        entry = MemoryEntry(
            session_id="s1", key="k1",
            content="x", source_reference="main.py"
        )
        tag = classifier.classify(entry)
        stale_after = classifier.compute_stale_after(tag)
        assert stale_after is not None
        assert stale_after > datetime.now(timezone.utc)

    def test_stale_after_none_for_assumed(self, classifier):
        entry = MemoryEntry(session_id="s1", key="k1", content="x")
        tag = classifier.classify(entry)
        stale_after = classifier.compute_stale_after(tag)
        assert stale_after is None


# ---------------------------------------------------------------------------
# Audit Logger Tests
# ---------------------------------------------------------------------------

class TestAuditLogger:

    def _make_record(self, session_id="sess1", key="k1"):
        return AuditRecord(
            entry_id="entry-1",
            session_id=session_id,
            key=key,
            operation="WRITE_APPROVED",
            epistemic_tag=EpistemicTag.VERIFIED,
            critique_verdict=CritiqueVerdict.PASS,
        )

    def test_record_and_query(self, tmp_db):
        logger = AuditLogger(db_path=tmp_db)
        record = self._make_record()
        logger.record(record)

        results = logger.query(session_id="sess1")
        assert len(results) == 1
        assert results[0].session_id == "sess1"
        assert results[0].epistemic_tag == EpistemicTag.VERIFIED

    def test_filter_by_key(self, tmp_db):
        logger = AuditLogger(db_path=tmp_db)
        logger.record(self._make_record(key="alpha"))
        logger.record(self._make_record(key="beta"))

        results = logger.query(key="alpha")
        assert len(results) == 1
        assert results[0].key == "alpha"

    def test_filter_by_tag(self, tmp_db):
        logger = AuditLogger(db_path=tmp_db)
        r1 = self._make_record()
        r1.epistemic_tag = EpistemicTag.VERIFIED
        r2 = self._make_record()
        r2.epistemic_tag = EpistemicTag.ASSUMED
        r2.entry_id = "entry-2"
        logger.record(r1)
        logger.record(r2)

        results = logger.query(epistemic_tag=EpistemicTag.ASSUMED)
        assert len(results) == 1
        assert results[0].epistemic_tag == EpistemicTag.ASSUMED

    def test_session_summary(self, tmp_db):
        logger = AuditLogger(db_path=tmp_db)
        logger.record(self._make_record(session_id="s42"))
        summary = logger.get_session_summary("s42")
        assert summary["total"] == 1
        assert summary["approved"] == 1


# ---------------------------------------------------------------------------
# Pipeline Integration Tests (critique agent mocked)
# ---------------------------------------------------------------------------

class TestMEMOIRPipeline:

    def _mock_pipeline(self, tmp_db, tmp_workspace, verdict=CritiqueVerdict.PASS):
        """Build a pipeline with the critique agent mocked."""
        pipeline = MEMOIRPipeline(
            db_path=tmp_db,
            workspace_root=tmp_workspace,
        )
        pipeline._critique.critique = MagicMock(
            return_value=(verdict, f"Mocked verdict: {verdict.value}", 42.0)
        )
        return pipeline

    def test_approved_write_returns_true(self, tmp_db, tmp_workspace, sample_entry):
        pipeline = self._mock_pipeline(tmp_db, tmp_workspace, CritiqueVerdict.PASS)
        result = pipeline.validate(sample_entry)
        assert result.approved is True
        assert result.critique_verdict == CritiqueVerdict.PASS

    def test_rejected_write_returns_false(self, tmp_db, tmp_workspace, sample_entry):
        pipeline = self._mock_pipeline(tmp_db, tmp_workspace, CritiqueVerdict.REJECT)
        result = pipeline.validate(sample_entry)
        assert result.approved is False
        assert result.epistemic_tag == EpistemicTag.REJECTED

    def test_flagged_write_approved_with_flag(self, tmp_db, tmp_workspace, sample_entry):
        pipeline = self._mock_pipeline(tmp_db, tmp_workspace, CritiqueVerdict.FLAG)
        result = pipeline.validate(sample_entry)
        assert result.approved is True
        assert result.critique_verdict == CritiqueVerdict.FLAG

    def test_audit_record_written(self, tmp_db, tmp_workspace, sample_entry):
        pipeline = self._mock_pipeline(tmp_db, tmp_workspace)
        result = pipeline.validate(sample_entry)
        records = pipeline.query_audit(session_id=sample_entry.session_id)
        assert len(records) == 1
        assert records[0].audit_id == result.audit_id

    def test_stale_entry_detected(self, tmp_db, tmp_workspace):
        pipeline = self._mock_pipeline(tmp_db, tmp_workspace)
        entry = MemoryEntry(
            session_id="sess1",
            key="some_key",
            content="The service runs on port 8080.",
            source_reference="config.yaml",
        )
        old_verified = datetime.now(timezone.utc) - timedelta(hours=48)
        result = pipeline.validate(entry, existing_verified_at=old_verified)
        assert result.epistemic_tag == EpistemicTag.STALE

    def test_session_summary_after_writes(self, tmp_db, tmp_workspace):
        pipeline = self._mock_pipeline(tmp_db, tmp_workspace)
        for i in range(3):
            entry = MemoryEntry(
                session_id="summary-test",
                key=f"key_{i}",
                content=f"Fact {i}",
            )
            pipeline.validate(entry)
        summary = pipeline.get_session_summary("summary-test")
        assert summary["total"] == 3
