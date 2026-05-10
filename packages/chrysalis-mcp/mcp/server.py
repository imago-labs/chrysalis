"""
MEMOIR MCP Server
-----------------
Drop-in MCP server that exposes MEMOIR's validation pipeline
as tools consumable by Claude Code and any MCP-compatible agent.

Add to claude_desktop_config.json:
{
  "mcpServers": {
    "memoir": {
      "command": "python",
      "args": ["-m", "memoir.mcp.server"],
      "env": {
        "ANTHROPIC_API_KEY": "your-key",
        "MEMOIR_DB_PATH": "~/.memoir/audit.db",
        "MEMOIR_WORKSPACE_ROOT": "/path/to/your/project"
      }
    }
  }
}
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict

from memoir.core.pipeline import MEMOIRPipeline
from memoir.models.memory import EpistemicTag, CritiqueVerdict, MemoryEntry


# ---------------------------------------------------------------------------
# Server init
# ---------------------------------------------------------------------------

mcp = FastMCP("memoir_mcp")

# Pipeline singleton — initialized once per server process
_pipeline: Optional[MEMOIRPipeline] = None


def _get_pipeline() -> MEMOIRPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = MEMOIRPipeline(
            db_path=os.environ.get("MEMOIR_DB_PATH"),
            workspace_root=os.environ.get("MEMOIR_WORKSPACE_ROOT"),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
        )
    return _pipeline


# ---------------------------------------------------------------------------
# Input Models
# ---------------------------------------------------------------------------

class WriteMemoryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    session_id: str = Field(..., description="Current agent session ID", min_length=1)
    key: str = Field(..., description="Memory key (e.g., 'auth_system_design')", min_length=1, max_length=200)
    content: str = Field(..., description="The belief or fact to record", min_length=1, max_length=4000)
    source_reference: Optional[str] = Field(default=None, description="File path or URL this derives from")
    tool_call_id: Optional[str] = Field(default=None, description="Tool call ID that generated this")
    agent_id: Optional[str] = Field(default=None, description="Sub-agent ID if from a worker")
    human_session_context: Optional[str] = Field(default=None, description="What the human was working on")
    existing_memory_content: Optional[str] = Field(default=None, description="Current value for this key, if any")
    existing_verified_at: Optional[str] = Field(default=None, description="ISO datetime of last verification for this key")


class QueryAuditInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    session_id: Optional[str] = Field(default=None, description="Filter by session ID")
    key: Optional[str] = Field(default=None, description="Filter by memory key")
    epistemic_tag: Optional[str] = Field(default=None, description="Filter by tag: VERIFIED/INFERRED/ASSUMED/STALE/REJECTED")
    critique_verdict: Optional[str] = Field(default=None, description="Filter by verdict: PASS/FLAG/REJECT")
    after: Optional[str] = Field(default=None, description="Filter records after this ISO datetime")
    before: Optional[str] = Field(default=None, description="Filter records before this ISO datetime")
    limit: int = Field(default=20, ge=1, le=100, description="Max records to return")
    offset: int = Field(default=0, ge=0, description="Pagination offset")


class SessionSummaryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    session_id: str = Field(..., description="Session ID to summarize", min_length=1)


class VerifyBeliefInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    session_id: str = Field(..., description="Current session ID")
    belief: str = Field(..., description="The belief to verify", min_length=1, max_length=2000)
    source_reference: str = Field(..., description="File path or reference to check against", min_length=1)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="memoir_write_memory",
    annotations={
        "title": "Validated Memory Write",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)
async def memoir_write_memory(params: WriteMemoryInput) -> str:
    """
    Submit a proposed memory write to the MEMOIR validation pipeline.

    Runs epistemic classification, ground truth verification (if source
    reference provided), and a critique agent pass before approving
    or rejecting the write. All operations are logged to the audit trail.

    Use this instead of writing directly to MEMORY.md. Every write
    gets an epistemic tag (VERIFIED/INFERRED/ASSUMED/STALE) and a
    critique verdict (PASS/FLAG/REJECT).

    Args:
        params (WriteMemoryInput): Memory entry details including:
            - session_id (str): Current agent session identifier
            - key (str): Memory key for indexing
            - content (str): The belief or fact to record
            - source_reference (Optional[str]): Source file or URL
            - tool_call_id (Optional[str]): Generating tool call ID
            - agent_id (Optional[str]): Sub-agent identifier
            - human_session_context (Optional[str]): Human authorization context
            - existing_memory_content (Optional[str]): Current value for this key
            - existing_verified_at (Optional[str]): ISO datetime of last verification

    Returns:
        str: JSON containing ValidationResult with approval status,
             epistemic tag, critique verdict, and audit ID.
    """
    pipeline = _get_pipeline()

    existing_verified_at = None
    if params.existing_verified_at:
        try:
            existing_verified_at = datetime.fromisoformat(params.existing_verified_at)
        except ValueError:
            pass

    entry = MemoryEntry(
        session_id=params.session_id,
        key=params.key,
        content=params.content,
        source_reference=params.source_reference,
        tool_call_id=params.tool_call_id,
        agent_id=params.agent_id,
        human_session_context=params.human_session_context,
    )

    result = pipeline.validate(
        entry=entry,
        existing_verified_at=existing_verified_at,
        existing_memory_content=params.existing_memory_content,
    )

    return json.dumps(result.model_dump(), indent=2, default=str)


@mcp.tool(
    name="memoir_query_audit",
    annotations={
        "title": "Query Audit Log",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def memoir_query_audit(params: QueryAuditInput) -> str:
    """
    Query the MEMOIR audit log with optional filters.

    Returns the chain of memory operations for a session, key, time window,
    or epistemic tag. Use this to reconstruct agent reasoning history
    or investigate a failure.

    Args:
        params (QueryAuditInput): Query filters including session_id, key,
            epistemic_tag, critique_verdict, after, before, limit, offset.

    Returns:
        str: JSON array of AuditRecord objects matching the query.
    """
    pipeline = _get_pipeline()

    tag = EpistemicTag(params.epistemic_tag) if params.epistemic_tag else None
    verdict = CritiqueVerdict(params.critique_verdict) if params.critique_verdict else None
    after = datetime.fromisoformat(params.after) if params.after else None
    before = datetime.fromisoformat(params.before) if params.before else None

    records = pipeline.query_audit(
        session_id=params.session_id,
        key=params.key,
        epistemic_tag=tag,
        critique_verdict=verdict,
        after=after,
        before=before,
        limit=params.limit,
        offset=params.offset,
    )

    return json.dumps(
        [r.model_dump() for r in records],
        indent=2,
        default=str,
    )


@mcp.tool(
    name="memoir_session_summary",
    annotations={
        "title": "Session Audit Summary",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def memoir_session_summary(params: SessionSummaryInput) -> str:
    """
    Return a summary of all memory operations for a session.

    Shows total writes, breakdown by epistemic tag and critique verdict,
    and counts of approved/flagged/rejected operations.

    Args:
        params (SessionSummaryInput): Session ID to summarize.

    Returns:
        str: JSON summary with counts by tag and verdict.
    """
    pipeline = _get_pipeline()
    summary = pipeline.get_session_summary(params.session_id)
    return json.dumps(summary, indent=2)


@mcp.tool(
    name="memoir_verify_belief",
    annotations={
        "title": "On-Demand Belief Verification",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def memoir_verify_belief(params: VerifyBeliefInput) -> str:
    """
    Verify a specific belief against a source reference on demand.

    Creates a temporary MemoryEntry and runs it through the full
    validation pipeline without committing to the memory store.
    Returns the critique verdict and reasoning.

    Use this when you want to check a belief before acting on it,
    without writing anything to memory.

    Args:
        params (VerifyBeliefInput): Belief text and source reference to check against.

    Returns:
        str: JSON ValidationResult with verdict and critique reasoning.
    """
    pipeline = _get_pipeline()

    entry = MemoryEntry(
        session_id=params.session_id,
        key=f"_verify__{params.session_id[:8]}",
        content=params.belief,
        source_reference=params.source_reference,
    )

    result = pipeline.validate(entry=entry)
    return json.dumps(result.model_dump(), indent=2, default=str)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
