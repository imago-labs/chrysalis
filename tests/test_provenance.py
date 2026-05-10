"""
Cross-Agent Provenance Tests
------------------------------
Tests for AGENT_DERIVED handling, derivation graph, and quality propagation.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone

import pytest

from memoir.provenance.agent_derived import (
    HANDOFF_DISCOUNT,
    AgentDerivedMetadata,
    compute_discounted_bqs,
    create_agent_derived_metadata,
)
from memoir.provenance.graph import DerivationEdge, DerivationGraph
from memoir.provenance.propagation import (
    PropagationResult,
    propagate_rejection,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_graph_db(tmp_path):
    return str(tmp_path / "test_derivation.db")


# ---------------------------------------------------------------------------
# Agent Derived Tests
# ---------------------------------------------------------------------------

class TestAgentDerived:

    def test_discount_single_handoff(self):
        discounted = compute_discounted_bqs(0.8, handoff_count=1)
        assert abs(discounted - 0.8 * HANDOFF_DISCOUNT) < 0.001

    def test_discount_multiple_handoffs(self):
        discounted = compute_discounted_bqs(0.8, handoff_count=3)
        expected = 0.8 * (HANDOFF_DISCOUNT ** 3)
        assert abs(discounted - round(expected, 4)) < 0.001

    def test_discount_never_below_zero(self):
        discounted = compute_discounted_bqs(0.1, handoff_count=100)
        assert discounted >= 0.0

    def test_discount_never_above_one(self):
        discounted = compute_discounted_bqs(1.0, handoff_count=0)
        assert discounted <= 1.0

    def test_create_metadata_first_handoff(self):
        meta = create_agent_derived_metadata(
            source_agent_id="agent-A",
            original_attestation_id="attest-123",
            original_epistemic_tag="VERIFIED",
            original_bqs=0.85,
        )
        assert meta.source_agent_id == "agent-A"
        assert meta.handoff_count == 1
        assert meta.discounted_bqs < 0.85
        assert meta.derivation_chain == ["agent-A"]

    def test_create_metadata_chain_grows(self):
        meta = create_agent_derived_metadata(
            source_agent_id="agent-B",
            original_attestation_id="attest-456",
            original_epistemic_tag="VERIFIED",
            original_bqs=0.85,
            existing_chain=["agent-A"],
        )
        assert meta.handoff_count == 2
        assert meta.derivation_chain == ["agent-A", "agent-B"]
        assert meta.discounted_bqs < compute_discounted_bqs(0.85, 1)

    def test_metadata_model_validation(self):
        meta = AgentDerivedMetadata(
            source_agent_id="a",
            original_epistemic_tag="VERIFIED",
            original_bqs=0.9,
            handoff_count=1,
            discounted_bqs=0.765,
        )
        assert meta.original_bqs == 0.9


# ---------------------------------------------------------------------------
# Derivation Graph Tests
# ---------------------------------------------------------------------------

class TestDerivationGraph:

    def test_add_and_get_children(self, tmp_graph_db):
        graph = DerivationGraph(db_path=tmp_graph_db)
        edge = DerivationEdge(
            parent_attestation_id="parent-1",
            child_attestation_id="child-1",
            parent_agent_id="agent-A",
            child_agent_id="agent-B",
            parent_entry_id="entry-A1",
            child_entry_id="entry-B1",
            original_bqs=0.85,
            discounted_bqs=0.7225,
            handoff_count=1,
        )
        graph.add_edge(edge)

        children = graph.get_children("parent-1")
        assert len(children) == 1
        assert children[0].child_agent_id == "agent-B"

    def test_get_parents(self, tmp_graph_db):
        graph = DerivationGraph(db_path=tmp_graph_db)
        edge = DerivationEdge(
            parent_attestation_id="p1",
            child_attestation_id="c1",
            parent_agent_id="a1",
            child_agent_id="a2",
            parent_entry_id="e1",
            child_entry_id="e2",
            original_bqs=0.8,
            discounted_bqs=0.68,
            handoff_count=1,
        )
        graph.add_edge(edge)

        parents = graph.get_parents("c1")
        assert len(parents) == 1
        assert parents[0].parent_agent_id == "a1"

    def test_get_all_descendants_chain(self, tmp_graph_db):
        graph = DerivationGraph(db_path=tmp_graph_db)
        # A -> B -> C chain
        graph.add_edge(DerivationEdge(
            parent_attestation_id="a1", child_attestation_id="b1",
            parent_agent_id="A", child_agent_id="B",
            parent_entry_id="eA", child_entry_id="eB",
            original_bqs=0.9, discounted_bqs=0.765, handoff_count=1,
        ))
        graph.add_edge(DerivationEdge(
            parent_attestation_id="b1", child_attestation_id="c1",
            parent_agent_id="B", child_agent_id="C",
            parent_entry_id="eB", child_entry_id="eC",
            original_bqs=0.765, discounted_bqs=0.65, handoff_count=2,
        ))

        descendants = graph.get_all_descendants("a1")
        assert len(descendants) == 2
        child_ids = {d.child_attestation_id for d in descendants}
        assert "b1" in child_ids
        assert "c1" in child_ids

    def test_no_children(self, tmp_graph_db):
        graph = DerivationGraph(db_path=tmp_graph_db)
        children = graph.get_children("nonexistent")
        assert children == []

    def test_duplicate_edge_ignored(self, tmp_graph_db):
        graph = DerivationGraph(db_path=tmp_graph_db)
        edge = DerivationEdge(
            parent_attestation_id="p", child_attestation_id="c",
            parent_agent_id="a1", child_agent_id="a2",
            parent_entry_id="e1", child_entry_id="e2",
            original_bqs=0.8, discounted_bqs=0.68, handoff_count=1,
        )
        graph.add_edge(edge)
        graph.add_edge(edge)  # should not raise
        children = graph.get_children("p")
        assert len(children) == 1

    def test_get_agent_derivations(self, tmp_graph_db):
        graph = DerivationGraph(db_path=tmp_graph_db)
        graph.add_edge(DerivationEdge(
            parent_attestation_id="p1", child_attestation_id="c1",
            parent_agent_id="A", child_agent_id="B",
            parent_entry_id="e1", child_entry_id="e2",
            original_bqs=0.8, discounted_bqs=0.68, handoff_count=1,
        ))
        derivations = graph.get_agent_derivations("B")
        assert len(derivations) == 1


# ---------------------------------------------------------------------------
# Propagation Tests
# ---------------------------------------------------------------------------

class TestPropagation:

    def test_propagate_rejection(self, tmp_graph_db):
        graph = DerivationGraph(db_path=tmp_graph_db)
        graph.add_edge(DerivationEdge(
            parent_attestation_id="rejected-1", child_attestation_id="child-1",
            parent_agent_id="A", child_agent_id="B",
            parent_entry_id="eA", child_entry_id="eB",
            original_bqs=0.8, discounted_bqs=0.68, handoff_count=1,
        ))
        graph.add_edge(DerivationEdge(
            parent_attestation_id="rejected-1", child_attestation_id="child-2",
            parent_agent_id="A", child_agent_id="C",
            parent_entry_id="eA", child_entry_id="eC",
            original_bqs=0.8, discounted_bqs=0.68, handoff_count=1,
        ))

        result = propagate_rejection(graph, "rejected-1")
        assert isinstance(result, PropagationResult)
        assert result.total_affected == 2
        assert len(result.events) == 2

    def test_propagate_no_descendants(self, tmp_graph_db):
        graph = DerivationGraph(db_path=tmp_graph_db)
        result = propagate_rejection(graph, "orphan-1")
        assert result.total_affected == 0
        assert len(result.events) == 0

    def test_propagation_chain(self, tmp_graph_db):
        graph = DerivationGraph(db_path=tmp_graph_db)
        # A -> B -> C
        graph.add_edge(DerivationEdge(
            parent_attestation_id="root", child_attestation_id="mid",
            parent_agent_id="A", child_agent_id="B",
            parent_entry_id="eA", child_entry_id="eB",
            original_bqs=0.9, discounted_bqs=0.765, handoff_count=1,
        ))
        graph.add_edge(DerivationEdge(
            parent_attestation_id="mid", child_attestation_id="leaf",
            parent_agent_id="B", child_agent_id="C",
            parent_entry_id="eB", child_entry_id="eC",
            original_bqs=0.765, discounted_bqs=0.65, handoff_count=2,
        ))

        result = propagate_rejection(graph, "root")
        assert result.total_affected == 2
