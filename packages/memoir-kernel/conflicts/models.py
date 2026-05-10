"""
Conflict Detection Data Models
-------------------------------
These are re-exported from memoir.models.memory for convenience,
plus some conflict-specific types used internally by the detector.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from memoir.models.memory import (
    ConflictRecord,
    ConflictType,
    ResolutionStrategy,
)


class ConflictCandidate(BaseModel):
    """
    A pair of beliefs flagged as potentially conflicting.
    The detector produces these; the resolver decides what to do.
    """
    model_config = ConfigDict(extra="forbid")

    belief_a_id: str
    belief_a_key: str
    belief_a_content: str
    belief_a_tag: str
    belief_a_bqs: Optional[float] = None

    belief_b_id: str
    belief_b_key: str
    belief_b_content: str
    belief_b_tag: str
    belief_b_bqs: Optional[float] = None

    conflict_type: ConflictType
    similarity_score: float = Field(ge=0.0, le=1.0, default=0.0)
    description: str


# Re-export for convenience
__all__ = [
    "ConflictCandidate",
    "ConflictRecord",
    "ConflictType",
    "ResolutionStrategy",
]
