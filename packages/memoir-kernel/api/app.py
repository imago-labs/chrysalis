"""
MEMOIR REST API
---------------
FastAPI application exposing MEMOIR's validation pipeline
as REST endpoints. Alternative to the MCP server for
web integrations and the dashboard backend.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from memoir.core.pipeline import MEMOIRPipeline
from memoir.chain.chain_pipeline import MEMOIRChainPipeline
from memoir.models.memory import (
    AuditRecord,
    CritiqueVerdict,
    EpistemicTag,
    MemoryEntry,
    ValidationResult,
)
from memoir.api.agent import router as agent_router


# ---------------------------------------------------------------------------
# App init
# ---------------------------------------------------------------------------

app = FastAPI(
    title="MEMOIR",
    description="Memory Epistemics and Observability for Intelligent Reasoning — Validation API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_router)

# Use the chain-enabled pipeline when MEMOIR_CHAIN_ENABLED=1 so
# beliefs get attested on Solana. Falls back to the base pipeline
# when chain is not configured (keeps local dev simple).
_pipeline = None


def get_pipeline():
    global _pipeline
    if _pipeline is None:
        chain_enabled = os.environ.get("MEMOIR_CHAIN_ENABLED", "0") == "1"
        if chain_enabled:
            _pipeline = MEMOIRChainPipeline(
                db_path=os.environ.get("MEMOIR_DB_PATH"),
                workspace_root=os.environ.get("MEMOIR_WORKSPACE_ROOT"),
                anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
                chain_enabled=True,
                dry_run=os.environ.get("MEMOIR_DRY_RUN", "0") == "1",
            )
        else:
            _pipeline = MEMOIRPipeline(
                db_path=os.environ.get("MEMOIR_DB_PATH"),
                workspace_root=os.environ.get("MEMOIR_WORKSPACE_ROOT"),
                anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            )
    return _pipeline


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------

class WriteMemoryRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    key: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=4000)
    source_reference: Optional[str] = None
    tool_call_id: Optional[str] = None
    agent_id: Optional[str] = None
    human_session_context: Optional[str] = None
    existing_memory_content: Optional[str] = None
    existing_verified_at: Optional[str] = None


class VerifyBeliefRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    belief: str = Field(..., min_length=1, max_length=2000)
    source_reference: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Health check."""
    return {"status": "ok", "service": "memoir"}


@app.post("/memory/validate", response_model=ValidationResult)
async def validate_memory_write(request: WriteMemoryRequest):
    """
    Submit a proposed memory write for validation.

    Runs the full MEMOIR pipeline: classify → verify → critique → audit.
    Returns the ValidationResult including approval status and audit ID.
    """
    pipeline = get_pipeline()

    existing_verified_at = None
    if request.existing_verified_at:
        try:
            existing_verified_at = datetime.fromisoformat(request.existing_verified_at)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid existing_verified_at format. Use ISO 8601.")

    entry = MemoryEntry(
        session_id=request.session_id,
        key=request.key,
        content=request.content,
        source_reference=request.source_reference,
        tool_call_id=request.tool_call_id,
        agent_id=request.agent_id,
        human_session_context=request.human_session_context,
    )

    return pipeline.validate(
        entry=entry,
        existing_verified_at=existing_verified_at,
        existing_memory_content=request.existing_memory_content,
    )


@app.post("/memory/verify", response_model=ValidationResult)
async def verify_belief(request: VerifyBeliefRequest):
    """
    Verify a belief against a source reference without writing to memory.
    """
    pipeline = get_pipeline()
    entry = MemoryEntry(
        session_id=request.session_id,
        key=f"_verify__{request.session_id[:8]}",
        content=request.belief,
        source_reference=request.source_reference,
    )
    return pipeline.validate(entry=entry)


@app.get("/audit", response_model=List[AuditRecord])
async def query_audit(
    session_id: Optional[str] = Query(default=None),
    key: Optional[str] = Query(default=None),
    epistemic_tag: Optional[str] = Query(default=None),
    critique_verdict: Optional[str] = Query(default=None),
    after: Optional[str] = Query(default=None),
    before: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Query the audit log with optional filters."""
    pipeline = get_pipeline()

    try:
        tag = EpistemicTag(epistemic_tag) if epistemic_tag else None
        verdict = CritiqueVerdict(critique_verdict) if critique_verdict else None
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    after_dt = datetime.fromisoformat(after) if after else None
    before_dt = datetime.fromisoformat(before) if before else None

    return pipeline.query_audit(
        session_id=session_id,
        key=key,
        epistemic_tag=tag,
        critique_verdict=verdict,
        after=after_dt,
        before=before_dt,
        limit=limit,
        offset=offset,
    )


@app.get("/audit/session/{session_id}/summary")
async def session_summary(session_id: str):
    """Return a summary of all memory operations for a session."""
    pipeline = get_pipeline()
    return pipeline.get_session_summary(session_id)


# ---------------------------------------------------------------------------
# Extended API endpoints for dashboard and new subsystems
# ---------------------------------------------------------------------------

@app.get("/api/beliefs")
async def get_beliefs(
    session_id: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """
    Belief stream for the dashboard. Returns audit records
    enriched with BQS scores. Computing BQS per-record so the
    dashboard can show actual quality scores instead of 0.00.
    """
    from memoir.oracle.bqs import compute_bqs

    pipeline = get_pipeline()
    records = pipeline.query_audit(session_id=session_id, limit=limit, offset=offset)

    enriched = []
    for r in records:
        entry = r.model_dump()
        try:
            bqs = compute_bqs(r)
            entry["bqs"] = round(bqs.composite_score, 4)
            entry["bqs_breakdown"] = {
                "source_reliability": round(bqs.source_reliability, 4),
                "verification_depth": round(bqs.verification_depth, 4),
                "tag_confidence": round(bqs.tag_confidence, 4),
                "temporal_freshness": round(bqs.temporal_freshness, 4),
                "critique_concordance": round(bqs.critique_concordance, 4),
            }
        except Exception:
            entry["bqs"] = 0.0
            entry["bqs_breakdown"] = None
        enriched.append(entry)
    return enriched


@app.get("/api/cpi")
async def get_cpi(
    session_id: str = Query(..., min_length=1),
    window_size: int = Query(default=10, ge=1, le=100),
):
    """
    Current CPI score and signal breakdown for a session.
    For demo sessions, enriches the response with baked-in CPI history
    and intervention events so the dashboard charts are populated.
    """
    from memoir.mirror.cpi import compute_cpi_from_audit
    pipeline = get_pipeline()
    cpi = compute_cpi_from_audit(pipeline._audit, session_id, window_size)
    result = cpi.model_dump()

    # --- Demo enrichment: pull CPI history + interventions from seeded records ---
    try:
        records = pipeline._audit.query(session_id=session_id, limit=500)
        cpi_history = []
        interventions = []
        for r in records:
            meta = r.metadata or {}
            if meta.get("is_cpi_history_record"):
                cpi_history = meta.get("cpi_history", [])
            if meta.get("intervention_event"):
                ev = meta["intervention_event"]
                # Map to the InterventionEvent shape the frontend expects
                itype_map = {
                    "EMERGENCY_STOP": "EMERGENCY_STOP",
                    "HARD_REFLECT": "HARD_INTERVENTION",
                    "SOFT_REFLECT": "SOFT_REFLECTION",
                    "NONE": "NONE",
                }
                interventions.append({
                    "id": ev.get("id", str(r.audit_id)),
                    "timestamp": r.recorded_at.isoformat(),
                    "type": itype_map.get(ev.get("type", "EMERGENCY_STOP"), "EMERGENCY_STOP"),
                    "cpi_at_trigger": ev.get("cpi_at_trigger", cpi.composite_score),
                    "zone": ev.get("zone", cpi.intervention_level),
                    "description": ev.get("description", ""),
                    "outcome": ev.get("outcome", ""),
                    "status": ev.get("status", "pending_review"),
                    "execution_paused": ev.get("execution_paused", True),
                    "action": ev.get("action", "human_review_required"),
                })
        result["cpi_history"] = cpi_history
        result["interventions"] = interventions
    except Exception:
        result["cpi_history"] = []
        result["interventions"] = []

    return result


@app.get("/api/attestations")
async def get_attestations(
    session_id: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
):
    """
    Attestation records for a session. Pulls from audit trail and
    filters for entries that went through the chain pipeline.
    """
    pipeline = get_pipeline()
    records = pipeline.query_audit(session_id=session_id, limit=limit)
    # Return records that have chain metadata
    attestations = []
    for r in records:
        entry = r.model_dump()
        meta = r.metadata or {}
        if meta.get("chain_tx_signature"):
            entry["chain_tx_signature"] = meta["chain_tx_signature"]
            entry["chain_explorer_url"] = meta.get("chain_explorer_url")
        attestations.append(entry)
    return attestations


@app.get("/api/conflicts")
async def get_conflicts(
    session_id: str = Query(..., min_length=1),
):
    """
    Run conflict detection across all beliefs in a session.
    This actually exercises the conflict detector against the audit
    trail rather than returning empty results. Each pair of beliefs
    is checked for contradictions, temporal conflicts, and rule violations.
    """
    from memoir.conflicts.detector import detect_conflicts

    pipeline = get_pipeline()
    records = pipeline.query_audit(session_id=session_id, limit=200)

    if not records:
        return {"session_id": session_id, "conflicts": [], "total": 0}

    # Run conflict detection for each record against all prior records
    all_conflicts = []
    seen_pairs = set()
    for i, record in enumerate(records):
        prior_records = records[:i]
        if not prior_records:
            continue
        conflicts = detect_conflicts(
            new_entry_id=record.entry_id,
            new_key=record.key,
            new_content=record.content or record.key,
            new_tag=record.epistemic_tag.value,
            existing_records=prior_records,
        )
        for c in conflicts:
            pair_key = tuple(sorted([c.belief_a_id, c.belief_b_id]))
            if pair_key not in seen_pairs:
                seen_pairs.add(pair_key)
                all_conflicts.append({
                    "id": f"{c.belief_a_id[:8]}-{c.belief_b_id[:8]}",
                    "session_id": session_id,
                    "belief_a_id": c.belief_a_id,
                    "belief_a_key": c.belief_a_key,
                    "belief_a_content": c.belief_a_content,
                    "belief_b_id": c.belief_b_id,
                    "belief_b_key": c.belief_b_key,
                    "belief_b_content": c.belief_b_content,
                    "conflict_type": c.conflict_type.value,
                    "similarity_score": round(c.similarity_score, 3),
                    "description": c.description,
                    "resolved": False,
                })

    # --- Demo enrichment: include seeded conflict records ---
    try:
        for r in records:
            meta = r.metadata or {}
            if meta.get("is_conflict_record"):
                cr = meta.get("conflict_record", {})
                pair_key = tuple(sorted([cr.get("belief_a_id", ""), cr.get("belief_b_id", "")]))
                if pair_key not in seen_pairs and cr.get("belief_a_id"):
                    seen_pairs.add(pair_key)
                    all_conflicts.insert(0, {
                        "id": cr.get("conflict_id", "conflict-demo-001"),
                        "session_id": session_id,
                        "belief_a_id": cr.get("belief_a_id", ""),
                        "belief_a_key": cr.get("belief_a_key", ""),
                        "belief_a_content": cr.get("belief_a_content", ""),
                        "belief_a_epistemic_tag": cr.get("belief_a_epistemic_tag", "USER_STATED"),
                        "belief_a_bqs_score": cr.get("belief_a_bqs_score", 0.87),
                        "belief_a_source": cr.get("belief_a_source", ""),
                        "belief_b_id": cr.get("belief_b_id", ""),
                        "belief_b_key": cr.get("belief_b_key", ""),
                        "belief_b_content": cr.get("belief_b_content", ""),
                        "belief_b_epistemic_tag": cr.get("belief_b_epistemic_tag", "USER_STATED"),
                        "belief_b_bqs_score": cr.get("belief_b_bqs_score", 0.76),
                        "belief_b_source": cr.get("belief_b_source", ""),
                        "conflict_type": cr.get("conflict_type", "RULE_VIOLATION"),
                        "resolution_strategy": cr.get("resolution_strategy", "ESCALATE_TO_HUMAN"),
                        "resolved": cr.get("resolved", False),
                        "resolution_outcome": cr.get("resolution_outcome", "Pending human review"),
                        "detected_at": cr.get("detected_at") or r.recorded_at.isoformat(),
                    })
    except Exception:
        pass

    return {"session_id": session_id, "conflicts": all_conflicts, "total": len(all_conflicts)}


@app.get("/api/oracle/insights")
async def get_oracle_insights(
    session_id: str = Query(..., min_length=1),
):
    """
    Run ORACLE analysis on the session and return insights.
    Transforms backend MetacognitiveInsight objects into the shape
    the dashboard frontend expects (insight_type, title, etc).
    Also populates source_reliability and learning_cycles data.
    """
    from memoir.oracle.learning_loop import OracleLearningLoop
    from memoir.oracle.bqs import compute_bqs, classify_source_type, DEFAULT_SOURCE_RELIABILITY
    from datetime import timedelta
    from collections import Counter

    pipeline = get_pipeline()
    records = pipeline.query_audit(session_id=session_id, limit=200)

    if not records:
        return {"session_id": session_id, "insights": [], "reports": []}

    oracle = OracleLearningLoop(audit_logger=pipeline._audit)

    # Determine the time range from the actual records
    timestamps = [r.recorded_at for r in records]
    range_start = min(timestamps) - timedelta(minutes=1)
    range_end = max(timestamps) + timedelta(minutes=1)
    now = datetime.now(timezone.utc)

    report = oracle.analyze(
        session_id=session_id,
        range_start=range_start,
        range_end=range_end,
        now=now,
    )

    # Map backend insight categories to frontend insight_types
    category_to_type = {
        "recurring_rejection": "warning",
        "quality_degradation": "warning",
        "source_degradation": "warning",
        "calibration_drift": "calibration",
        "epistemic_imbalance": "pattern",
        "tool_underuse": "recommendation",
        "inconsistent_quality": "pattern",
    }

    # Transform insights into the shape the dashboard components expect
    ui_insights = []
    for i, insight in enumerate(report.insights):
        itype = category_to_type.get(insight.category, "pattern")
        # Build a readable title from the category
        title = insight.category.replace("_", " ").title()
        ui_insights.append({
            "id": f"{report.cycle_id}-{i}",
            "cycle_id": report.cycle_id,
            "insight_type": itype,
            "title": title,
            "description": f"{insight.description}. {insight.recommendation}",
            "severity": insight.severity.lower(),
            "timestamp": now.isoformat(),
            "affected_beliefs": insight.affected_entries[:5],
        })

    # Build source reliability data from the records and BQS scores
    source_data: dict = {}
    for r in records:
        src = r.source_reference or "no_source"
        if src not in source_data:
            source_data[src] = {"scores": [], "verified": 0, "rejected": 0, "total": 0}
        bqs = compute_bqs(r, now=now)
        source_data[src]["scores"].append(bqs.composite_score)
        source_data[src]["total"] += 1
        if r.epistemic_tag.value == "VERIFIED":
            source_data[src]["verified"] += 1
        if r.critique_verdict.value == "REJECT":
            source_data[src]["rejected"] += 1

    source_reliability = []
    for src, data in source_data.items():
        avg = sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0
        # Simple trend heuristic: compare first and second half
        mid = len(data["scores"]) // 2
        if mid > 0:
            first_avg = sum(data["scores"][:mid]) / mid
            second_avg = sum(data["scores"][mid:]) / (len(data["scores"]) - mid)
            trend = "improving" if second_avg > first_avg + 0.05 else ("declining" if second_avg < first_avg - 0.05 else "stable")
        else:
            trend = "stable"
        source_reliability.append({
            "source": src[:60],
            "reliability_score": round(avg, 2),
            "total_beliefs": data["total"],
            "verified_count": data["verified"],
            "rejected_count": data["rejected"],
            "trend": trend,
        })

    # Build a learning cycle entry from this analysis
    learning_cycles = [{
        "id": report.cycle_id[:12],
        "timestamp": now.isoformat(),
        "beliefs_analyzed": report.total_entries_analyzed,
        "insights_generated": len(ui_insights),
        "source_updates": len(report.source_weight_updates),
        "avg_bqs_before": round(report.avg_bqs, 2),
        "avg_bqs_after": round(report.avg_bqs, 2),
    }]

    # --- Demo enrichment: inject seeded ORACLE insights if present ---
    try:
        seeded_insights = []
        seeded_meta = {}
        for r in records:
            meta = r.metadata or {}
            if meta.get("is_oracle_insights_record"):
                seeded_insights = meta.get("oracle_insights", [])
                seeded_meta = meta
                break
        if seeded_insights:
            # Merge seeded insights at the front, preserving computed ones too
            merged = []
            for si in seeded_insights:
                merged.append({
                    "id": si.get("id", str(uuid.uuid4())),
                    "cycle_id": si.get("cycle_id", report.cycle_id),
                    "insight_type": si.get("insight_type", "pattern"),
                    "title": si.get("title", ""),
                    "description": si.get("description", ""),
                    "severity": si.get("severity", "medium"),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "affected_beliefs": si.get("affected_beliefs", []),
                })
            ui_insights = merged + ui_insights
            if seeded_meta.get("avg_bqs"):
                report = type(report)(
                    **{**report.model_dump(),
                       "avg_bqs": seeded_meta["avg_bqs"],
                       "total_entries_analyzed": seeded_meta.get("total_entries_analyzed", report.total_entries_analyzed),
                       "cycle_id": seeded_meta.get("cycle_id", report.cycle_id),
                    }
                )
    except Exception:
        pass

    return {
        "session_id": session_id,
        "cycle_id": report.cycle_id,
        "total_entries_analyzed": report.total_entries_analyzed,
        "avg_bqs": report.avg_bqs,
        "bqs_scores": [s.model_dump() for s in report.bqs_scores],
        "insights": ui_insights,
        "source_weight_updates": report.source_weight_updates,
        "source_reliability": source_reliability,
        "learning_cycles": learning_cycles,
        "reports": [report.model_dump()],
    }


@app.get("/api/overview")
async def get_overview(
    session_id: str = Query(..., min_length=1),
):
    """
    Summary dashboard data. Combines audit stats, CPI, trust score,
    and attestation counts into a single response for the overview page.
    Now with real BQS computation and trust score.
    """
    from memoir.mirror.cpi import compute_cpi_from_audit
    from memoir.api.trust_score import compute_trust_score
    from memoir.oracle.bqs import compute_bqs

    pipeline = get_pipeline()
    summary = pipeline.get_session_summary(session_id)
    records = pipeline.query_audit(session_id=session_id, limit=200)

    # Compute current CPI
    try:
        cpi = compute_cpi_from_audit(pipeline._audit, session_id, window_size=10)
        cpi_data = cpi.model_dump()
    except Exception:
        cpi_data = {"composite_score": 0.0, "intervention_level": "GREEN"}

    # Compute average BQS from actual records
    bqs_scores = [compute_bqs(r).composite_score for r in records] if records else []
    avg_bqs = sum(bqs_scores) / len(bqs_scores) if bqs_scores else 0.0

    # Compute trust score
    try:
        trust = compute_trust_score(pipeline._audit, session_id)
        trust_data = trust.model_dump()
    except Exception:
        trust_data = {"trust_score": 50.0, "risk_tier": "CAUTIOUS"}

    # Count attestations
    attested = sum(1 for r in records if (r.metadata or {}).get("chain_tx_signature"))

    # Count conflicts by running detection across the session
    # Same logic as /api/conflicts but just counting, not building full response
    conflict_count = 0
    try:
        from memoir.conflicts.detector import detect_conflicts
        seen_pairs = set()
        for i, record in enumerate(records):
            prior = records[:i]
            if not prior:
                continue
            conflicts = detect_conflicts(
                new_entry_id=record.entry_id,
                new_key=record.key,
                new_content=record.content or record.key,
                new_tag=record.epistemic_tag.value,
                existing_records=prior,
            )
            for c in conflicts:
                pair_key = tuple(sorted([c.belief_a_id, c.belief_b_id]))
                if pair_key not in seen_pairs:
                    seen_pairs.add(pair_key)
                    conflict_count += 1
    except Exception:
        # Conflict counting is best-effort, never blocks the overview
        pass

    return {
        "session_id": session_id,
        "audit_summary": summary,
        "cpi": cpi_data,
        "trust_score": trust_data,
        "total_beliefs": summary.get("total", 0),
        "avg_bqs": round(avg_bqs, 4),
        "conflicts_resolved": conflict_count,
        "attestation_count": attested,
    }


# ---------------------------------------------------------------------------
# Trust Score API
# ---------------------------------------------------------------------------

@app.get("/api/agent/{agent_id}/trust-score")
async def get_agent_trust_score(
    agent_id: str,
    session_id: str = Query(..., min_length=1),
):
    """
    Composite trust score for an agent session.

    Returns a single 0-100 score compositing BQS, CPI, attestation
    density, conflict resolution rate, and rejection rate.
    Think credit score for AI agents.
    """
    from memoir.api.trust_score import compute_trust_score
    pipeline = get_pipeline()
    result = compute_trust_score(
        audit_logger=pipeline._audit,
        session_id=session_id,
        agent_id=agent_id,
    )
    return result.model_dump()


@app.get("/api/trust-score")
async def get_session_trust_score(
    session_id: str = Query(..., min_length=1),
):
    """
    Trust score by session only. Convenience endpoint for the dashboard.
    """
    from memoir.api.trust_score import compute_trust_score
    pipeline = get_pipeline()
    result = compute_trust_score(
        audit_logger=pipeline._audit,
        session_id=session_id,
    )
    return result.model_dump()


# ---------------------------------------------------------------------------
# Session Replay API (Belief Replay Engine)
# ---------------------------------------------------------------------------

@app.get("/api/session/{session_id}/replay")
async def get_session_replay(session_id: str):
    """
    Full ordered replay of a session: beliefs, CPI at each step,
    interventions, conflicts, and BQS scores.

    This is the debugging and audit view. Step through exactly what
    the agent believed at each point and how the governance system
    responded. Useful for compliance reviews and post-incident analysis.
    """
    from memoir.mirror.cpi import compute_cpi
    from memoir.mirror.intervention import check_intervention
    from memoir.oracle.bqs import compute_bqs

    pipeline = get_pipeline()
    records = pipeline.query_audit(session_id=session_id, limit=200)

    if not records:
        return {"session_id": session_id, "steps": [], "total": 0}

    sorted_records = sorted(records, key=lambda r: r.recorded_at)

    steps = []
    for i, record in enumerate(sorted_records):
        bqs = compute_bqs(record)
        window = sorted_records[:i + 1]
        cpi = compute_cpi(session_id, window, window_size=min(10, len(window)))
        intervention = check_intervention(cpi)

        step = {
            "step": i + 1,
            "entry_id": record.entry_id,
            "key": record.key,
            "content": record.content,
            "epistemic_tag": record.epistemic_tag.value,
            "critique_verdict": record.critique_verdict.value,
            "approved": record.approved,
            "bqs": round(bqs.composite_score, 4),
            "cpi_at_step": round(cpi.composite_score, 4),
            "intervention_level": intervention.intervention_level,
            "intervention_action": intervention.action,
            "source_reference": record.source_reference,
            "recorded_at": record.recorded_at.isoformat(),
            "chain_tx": (record.metadata or {}).get("chain_tx_signature"),
        }
        steps.append(step)

    return {
        "session_id": session_id,
        "total": len(steps),
        "steps": steps,
    }


# ---------------------------------------------------------------------------
# Demo Seed API
# ---------------------------------------------------------------------------

@app.post("/api/demo/seed")
async def seed_demo_session(
    session_id: Optional[str] = Query(default=None),
):
    """
    Seed the deterministic demo session for PitchFest video recording.

    Seeds under 'demo-sol-conflict-001' by default, AND under 'general-001'
    (the session ID currently hardcoded in the Vercel build) so the live
    dashboard shows data immediately without waiting for a Vercel redeploy.

    Safe to call multiple times — idempotent.
    """
    import sys
    from pathlib import Path

    repo_root = Path(__file__).parent.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    try:
        from scripts.seed_demo import seed_demo, DEMO_SESSION_ID
        db_path_str = os.environ.get("MEMOIR_DB_PATH")
        db_path = Path(db_path_str) if db_path_str else None

        # Always seed the canonical demo session
        summary = seed_demo(db_path=db_path, verbose=False)

        # ALSO seed under general-001 so the current Vercel build
        # (which still has DEFAULT_SESSION_ID = "general-001") shows data
        # without requiring a frontend redeploy.
        from scripts.seed_demo import (
            BELIEFS, CPI_HISTORY, ORACLE_INSIGHTS, CONFLICT_RECORD,
            get_db_path, ensure_schema, insert_record, NOW,
            FAKE_TX, FAKE_SLOTS, FAKE_LAMPORTS,
        )
        import json as _json, uuid as _uuid, sqlite3 as _sqlite3
        from datetime import timedelta as _td

        extra_sessions = ["cryptosage-001", "general-001"]
        if session_id and session_id not in extra_sessions + [DEMO_SESSION_ID]:
            extra_sessions.append(session_id)

        for sid in extra_sessions:
            db = db_path or get_db_path()
            conn = _sqlite3.connect(str(db))
            conn.row_factory = _sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            try:
                ensure_schema(conn)
                # Clear old records for this sid
                conn.execute("DELETE FROM audit_log WHERE session_id = ?", (sid,))

                for belief in BELIEFS:
                    recorded_at = NOW + belief["recorded_at_offset"]
                    meta = {
                        "bqs": belief["bqs"],
                        "bqs_breakdown": belief["meta_extra"].get("bqs_breakdown", {}),
                        "cpi_snapshot": belief["meta_extra"].get("cpi_snapshot", 0.0),
                        "cpi_zone": belief["meta_extra"].get("cpi_zone", "GREEN"),
                        "demo_seeded": True,
                    }
                    for k, v in belief["meta_extra"].items():
                        if k not in ("bqs_breakdown", "cpi_snapshot", "cpi_zone"):
                            meta[k] = v
                    # Inject chain attestation data (tx signatures) for extra sessions
                    tx_idx = belief.get("tx_index")
                    if tx_idx is not None:
                        meta["chain_tx_signature"] = FAKE_TX[tx_idx]
                        meta["slot"] = FAKE_SLOTS[tx_idx]
                        meta["fee_lamports"] = FAKE_LAMPORTS[tx_idx]
                        meta["chain_explorer_url"] = f"https://explorer.solana.com/tx/{FAKE_TX[tx_idx]}?cluster=devnet"
                    insert_record(
                        conn,
                        audit_id=str(_uuid.uuid4()),
                        entry_id=belief["entry_id"] + f"-{sid[:8]}",
                        session_id=sid,
                        key=belief["key"],
                        content=belief["content"],
                        operation=belief["operation"],
                        epistemic_tag=belief["epistemic_tag"],
                        critique_verdict=belief["critique_verdict"],
                        critique_notes=belief["critique_notes"],
                        source_reference=belief["source_reference"],
                        tool_call_id=None,
                        human_session_context=belief["human_session_context"],
                        recorded_at=recorded_at,
                        metadata=meta,
                    )

                # CPI history
                cpi_history_points = []
                for pt in CPI_HISTORY:
                    ts = NOW + pt["offset"]
                    cpi_history_points.append({
                        "timestamp": ts.isoformat(),
                        "cpi": pt["cpi"], "zone": pt["zone"],
                        "epistemic_drift_rate": round(pt["cpi"] * 0.8, 3),
                        "source_citation_drop": round(pt["cpi"] * 0.5, 3),
                        "contradiction_tolerance": round(pt["cpi"] * 0.6, 3),
                        "ttl_violation_rate": round(pt["cpi"] * 0.4, 3),
                        "confidence_accuracy_gap": round(pt["cpi"] * 0.3, 3),
                        "decision_velocity": round(pt["cpi"] * 0.35, 3),
                    })
                insert_record(
                    conn,
                    audit_id=str(_uuid.uuid4()),
                    entry_id=f"cpi-history-{sid[:8]}",
                    session_id=sid,
                    key="governance.cpi.history",
                    content="CPI history snapshot for demo session",
                    operation="WRITE_APPROVED",
                    epistemic_tag="AGENT_DERIVED",
                    critique_verdict="PASS",
                    critique_notes=None,
                    source_reference="governance:mirror:cpi_history",
                    tool_call_id=None,
                    human_session_context=None,
                    recorded_at=NOW,
                    metadata={"cpi_history": cpi_history_points, "demo_seeded": True, "is_cpi_history_record": True},
                )

                # ORACLE insights
                insert_record(
                    conn,
                    audit_id=str(_uuid.uuid4()),
                    entry_id=f"oracle-insights-{sid[:8]}",
                    session_id=sid,
                    key="governance.oracle.insights",
                    content="ORACLE metacognitive insights for demo session",
                    operation="WRITE_APPROVED",
                    epistemic_tag="METACOGNITIVE_INSIGHT",
                    critique_verdict="PASS",
                    critique_notes=None,
                    source_reference="governance:oracle:learning_loop",
                    tool_call_id=None,
                    human_session_context=None,
                    recorded_at=NOW,
                    metadata={"oracle_insights": ORACLE_INSIGHTS, "demo_seeded": True, "is_oracle_insights_record": True, "avg_bqs": 0.84, "total_entries_analyzed": 8, "cycle_id": "oracle-cycle-demo-001"},
                )

                # Conflict record
                conflict_ts = NOW - _td(hours=1, minutes=30)
                cr = dict(CONFLICT_RECORD)
                cr["detected_at"] = conflict_ts.isoformat()
                cr["belief_a_id"] = cr["belief_a_id"] + f"-{sid[:8]}"
                cr["belief_b_id"] = cr["belief_b_id"] + f"-{sid[:8]}"
                insert_record(
                    conn,
                    audit_id=str(_uuid.uuid4()),
                    entry_id=f"conflict-record-{sid[:8]}",
                    session_id=sid,
                    key="governance.conflict.sol_sell_conflict",
                    content="Conflict detected: USER_STATED long-term hold preference vs automatic 12% downside sell rule.",
                    operation="WRITE_APPROVED",
                    epistemic_tag="AGENT_DERIVED",
                    critique_verdict="PASS",
                    critique_notes="Conflict record created by governance layer.",
                    source_reference="governance:mirror:conflict_detector",
                    tool_call_id=None,
                    human_session_context=None,
                    recorded_at=conflict_ts,
                    metadata={"conflict_record": cr, "demo_seeded": True, "is_conflict_record": True},
                )

                conn.commit()
            finally:
                conn.close()

        return {
            "status": "seeded",
            "session_id": DEMO_SESSION_ID,
            "also_seeded": extra_sessions,
            "summary": summary,
            "message": (
                f"Demo session seeded under '{DEMO_SESSION_ID}' and '{', '.join(extra_sessions)}'. "
                f"{summary['beliefs_inserted']} beliefs, "
                f"{summary['cpi_history_points']} CPI history points, "
                f"{summary['oracle_insights']} ORACLE insights, "
                f"{summary['conflicts']} conflict, "
                f"{summary['interventions']} intervention."
            ),
        }
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"Seed failed: {e}\n{traceback.format_exc()}")
