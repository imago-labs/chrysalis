# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
"""
Chrysalis SDK

Developer-facing wrapper for the Chrysalis platform HTTP API. Wraps belief
validation, audit log queries, and governance event streaming behind a typed
async client.

The SDK targets two deployment shapes:

  - Chrysalis Cloud (managed service at api.chrysalis.dev)
  - Self-hosted Chrysalis platform (any reachable base URL)

Both speak the same HTTP API. Pick the base URL and an API key at construction.

Quick start:

    from chrysalis_sdk import ChrysalisClient

    async with ChrysalisClient(
        base_url="https://api.chrysalis.dev",
        api_key="...",
    ) as client:
        result = await client.beliefs.validate(
            session_id="conv_xyz",
            key="user_preference",
            content="The user prefers brevity over depth.",
            source_reference="conv_xyz turn 14",
        )
        print(result.approved, result.epistemic_tag)
"""

from chrysalis_sdk.client import ChrysalisClient, ChrysalisError
from chrysalis_sdk.models import (
    AuditRecord,
    Belief,
    GovernanceEvent,
    SessionSummary,
    TrustScore,
    ValidationResult,
)

__all__ = [
    "ChrysalisClient",
    "ChrysalisError",
    "AuditRecord",
    "Belief",
    "GovernanceEvent",
    "SessionSummary",
    "TrustScore",
    "ValidationResult",
]

__version__ = "0.1.0"
