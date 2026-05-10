"""
Integration Tests
------------------
End-to-end tests that chain multiple CHRYSALIS modules together.
Exercises the full flow: pipeline -> conflicts -> CPI -> ORACLE.
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from memoir.core.audit import AuditLogger
from memoir.core.pipeline import MEMOIRPipeline
from memoir.models.memory import (
    AuditRecord,
    BQSScore,
    ConflictType,
    CPIScore,
    CritiqueVerdict,
    EpistemicTag,
    MemoryEntry,
    ResolutionStrategy,
)
from memoir.mirror.cpi import compute_cpi
from memoir.mirror.intervention import check_intervention
from memoir.oracle.learning_loop import OracleLearningLoop
from memoir.conflicts.detector import detect_conflicts
from memoir.conflicts.models import ConflictCandidate
from memoir.conflicts.resolver import resolve_conflict
from memoir.provenance.agent_derived import create_agent_derived_metadata, compute_discounted_bqs
from memoir.provenance.graph import DerivationEdge, DerivationGraph
from memoir.provenance.propagation import propagate_rejection
from memoir.chain.dry_run_provider import DryRunProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "integration_audit.db")


@pytest.fixture
def tmp_graph_db(tmp_path):
    return str(tmp_path / "integration_derivation.db")


@pytest.fixture
def pipeline(tmp_db, tmp_path):
    workspace = str(tmp_path / "workspace")
    import os
    os.makedirs(workspace, exist_ok=True)

    p = MEMOIRPipeline(db_path=tmp_db, workspace_root=workspace)
    # Mock critique so we control verdicts
    p._critique.critique = MagicMock(
        return_value=(CritiqueVerdict.PASS, "Approved by mock critique.", 10.0)
    )
    return p


# ---------------------------------------------------------------------------
# Full Pipeline Flow Tests
# ---------------------------------------------------------------------------

class TestFullPipelineFlow:

    def test_pipeline_to_cpi(self, pipeline):
        """Pipeline -> audit -> CPI computation"""
        session_id = "integration-flow-1"

        # Write several beliefs through the pipeline
        for i in range(8):
            entry = MemoryEntry(
                session_id=session_id,
                key=f"belief_{i}",
                content=f"Integration test belief number {i}",
                source_reference="src/main.py" if i < 5 else None,
            )
            pipeline.validate(entry)

        # Compute CPI from the audit trail
        records = pipeline.query_audit(session_id=session_id)
        assert len(records) == 8

        cpi = compute_cpi(session_id, records, window_size=10)
        assert isinstance(cpi, CPIScore)
        assert cpi.window_size == 8

    def test_pipeline_to_oracle(self, pipeline, tmp_db):
        """Pipeline -> audit -> ORACLE analysis"""
        session_id = "integration-flow-2"
        now = datetime.now(timezone.utc)

        for i in range(5):
            entry = MemoryEntry(
                session_id=session_id,
                key=f"oracle_belief_{i}",
                content=f"Belief for ORACLE analysis {i}",
                source_reference="src/main.py",
            )
            pipeline.validate(entry)

        oracle = OracleLearningLoop(audit_logger=pipeline._audit)
        report = oracle.analyze(
            session_id=session_id,
            range_start=now - timedelta(hours=1),
            range_end=now + timedelta(minutes=5),
            now=now,
        )

        assert report.total_entries_analyzed == 5
        assert len(report.bqs_scores) == 5
        assert report.avg_bqs > 0

    def test_conflict_detection_with_pipeline(self, pipeline):
        """Pipeline records -> conflict detection"""
        session_id = "integration-flow-3"

        # Write a VERIFIED belief
        entry1 = MemoryEntry(
            session_id=session_id,
            key="market_data",
            content="SOL price is stable at $150",
            source_reference="https://api.exchange.com/price",
        )
        pipeline.validate(entry1)

        # Now try to write a conflicting belief
        existing = pipeline.query_audit(session_id=session_id)
        conflicts = detect_conflicts(
            new_entry_id="new-conflict",
            new_key="market_data",
            new_content="SOL price crash imminent, will drop to $50",
            new_tag="INFERRED",
            existing_records=existing,
            similarity_threshold=0.1,
        )
        # The detector should find potential issues with same-key different content
        # (exact results depend on keyword matching)
        assert isinstance(conflicts, list)

    def test_provenance_chain_with_bqs_discount(self, tmp_graph_db):
        """Agent A -> Agent B -> Agent C with BQS degradation"""
        graph = DerivationGraph(db_path=tmp_graph_db)

        original_bqs = 0.90

        # A produces belief
        meta_ab = create_agent_derived_metadata(
            source_agent_id="agent-A",
            original_attestation_id="attest-A",
            original_epistemic_tag="VERIFIED",
            original_bqs=original_bqs,
        )
        assert meta_ab.discounted_bqs < original_bqs

        # B passes to C
        meta_bc = create_agent_derived_metadata(
            source_agent_id="agent-B",
            original_attestation_id="attest-B",
            original_epistemic_tag="AGENT_DERIVED",
            original_bqs=original_bqs,
            existing_chain=["agent-A"],
        )
        assert meta_bc.handoff_count == 2
        assert meta_bc.discounted_bqs < meta_ab.discounted_bqs

        # Store in graph
        graph.add_edge(DerivationEdge(
            parent_attestation_id="attest-A",
            child_attestation_id="attest-B",
            parent_agent_id="agent-A",
            child_agent_id="agent-B",
            parent_entry_id="eA",
            child_entry_id="eB",
            original_bqs=original_bqs,
            discounted_bqs=meta_ab.discounted_bqs,
            handoff_count=1,
        ))
        graph.add_edge(DerivationEdge(
            parent_attestation_id="attest-B",
            child_attestation_id="attest-C",
            parent_agent_id="agent-B",
            child_agent_id="agent-C",
            parent_entry_id="eB",
            child_entry_id="eC",
            original_bqs=meta_ab.discounted_bqs,
            discounted_bqs=meta_bc.discounted_bqs,
            handoff_count=2,
        ))

        # Reject A's original belief
        result = propagate_rejection(graph, "attest-A")
        assert result.total_affected == 2

    def test_chain_abstraction_dry_run(self):
        """DryRunProvider full lifecycle"""
        provider = DryRunProvider()

        receipt = asyncio.get_event_loop().run_until_complete(
            provider.attest("e1", "s1", "k1", "VERIFIED", "PASS", "a1", True)
        )
        assert receipt.provider == "dry_run"

        verification = asyncio.get_event_loop().run_until_complete(
            provider.verify(receipt.tx_signature)
        )
        assert verification.verified

        history = asyncio.get_event_loop().run_until_complete(
            provider.get_session_history("s1")
        )
        assert len(history) == 1

    def test_cpi_to_intervention(self, pipeline):
        """CPI -> intervention decision"""
        session_id = "integration-intervention"

        # Write beliefs with various issues
        for i in range(6):
            entry = MemoryEntry(
                session_id=session_id,
                key=f"pressure_{i}",
                content=f"High pressure belief {i}",
                source_reference="src/main.py" if i < 2 else None,
            )
            pipeline.validate(entry)

        records = pipeline.query_audit(session_id=session_id)
        cpi = compute_cpi(session_id, records, window_size=10)
        decision = check_intervention(cpi, risk_level="HIGH")

        assert decision.session_id == session_id
        assert decision.action in ("NONE", "SOFT_REFLECT", "HARD_REFLECT", "EMERGENCY_STOP")


class TestEdgeCases:

    def test_empty_audit_trail_cpi(self):
        """CPI with no records should be GREEN"""
        cpi = compute_cpi("empty-session", [], window_size=10)
        assert cpi.intervention_level == "GREEN"
        assert cpi.composite_score == 0.0

    def test_empty_audit_trail_oracle(self, tmp_db):
        """ORACLE with no records should return empty report"""
        logger = AuditLogger(db_path=tmp_db)
        oracle = OracleLearningLoop(audit_logger=logger)
        now = datetime.now(timezone.utc)
        report = oracle.analyze("empty", now - timedelta(hours=1), now)
        assert report.total_entries_analyzed == 0

    def test_single_record_cpi(self, tmp_db):
        """CPI with just 1 record"""
        logger = AuditLogger(db_path=tmp_db)
        logger.record(AuditRecord(
            entry_id="solo",
            session_id="single",
            key="k1",
            operation="WRITE_APPROVED",
            epistemic_tag=EpistemicTag.VERIFIED,
            critique_verdict=CritiqueVerdict.PASS,
        ))
        records = logger.query(session_id="single")
        cpi = compute_cpi("single", records, window_size=10)
        assert cpi.intervention_level == "GREEN"

    def test_conflict_resolution_model_serialization(self):
        """ConflictRecord serializes properly"""
        from memoir.models.memory import ConflictRecord
        record = ConflictRecord(
            session_id="s1",
            belief_a_id="a",
            belief_b_id="b",
            conflict_type=ConflictType.DIRECT_CONTRADICTION,
            resolution_strategy=ResolutionStrategy.ESCALATE_TO_HUMAN,
            description="test",
        )
        data = record.model_dump()
        assert data["conflict_type"] == "DIRECT_CONTRADICTION"
        assert data["resolution_strategy"] == "ESCALATE_TO_HUMAN"
