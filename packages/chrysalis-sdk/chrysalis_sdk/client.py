# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
"""
Async client for the Chrysalis platform HTTP API.

Single entry point: ChrysalisClient. Resource methods are grouped onto
namespaced sub-clients (client.beliefs, client.audit, client.agent,
client.events) to keep call sites readable.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from chrysalis_sdk.models import (
    AuditRecord,
    GovernanceEvent,
    SessionSummary,
    TrustScore,
    ValidationResult,
)


class ChrysalisError(RuntimeError):
    """Raised for non-2xx responses from the Chrysalis API."""

    def __init__(self, status_code: int, message: str, body: Any = None) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.body = body


class ChrysalisClient:
    """Async client for the Chrysalis platform.

    Args:
        base_url: API root, for example "https://api.chrysalis.dev" or
            "http://localhost:8000" for a self-hosted deployment.
        api_key: Shared API key. Sent as the X-API-Key header on every
            request. Required unless the deployment runs with
            CHRYSALIS_AUTH_REQUIRED=0.
        timeout: Request timeout in seconds. Defaults to 30.
        client: Optional preconfigured httpx.AsyncClient (useful for tests
            and for sharing a connection pool across SDK instances). When
            provided, the SDK does not own the client and will not close it.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._timeout = timeout
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            headers: dict[str, str] = {"Accept": "application/json"}
            if api_key:
                headers["X-API-Key"] = api_key
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=timeout,
            )
            self._owns_client = True

        # Namespaced sub-clients.
        self.beliefs = _BeliefsAPI(self)
        self.audit = _AuditAPI(self)
        self.agent = _AgentAPI(self)
        self.events = _EventsAPI(self)

    async def __aenter__(self) -> "ChrysalisClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- Low-level transport ------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> Any:
        resp = await self._client.request(
            method,
            path,
            params=params,
            json=json_body,
        )
        if resp.status_code >= 400:
            try:
                body = resp.json()
                message = body.get("detail") if isinstance(body, dict) else str(body)
            except Exception:
                body = resp.text
                message = body
            raise ChrysalisError(resp.status_code, message or "request failed", body)
        if not resp.content:
            return None
        return resp.json()

    # -- Health check (no auth required) -----------------------------------

    async def health(self) -> dict:
        """GET /health. Open to all callers, returns service status."""
        return await self._request("GET", "/health")


# ---------------------------------------------------------------------------
# Namespaced sub-clients
# ---------------------------------------------------------------------------


class _BeliefsAPI:
    """Endpoints for validating and listing beliefs."""

    def __init__(self, client: ChrysalisClient) -> None:
        self._c = client

    async def validate(
        self,
        *,
        session_id: str,
        key: str,
        content: str,
        source_reference: str | None = None,
        human_session_context: str | None = None,
    ) -> ValidationResult:
        """POST /memory/validate. Runs a belief through the MEMOIR pipeline.

        The pipeline applies the Critic, conflict detection, and any
        configured monitors before returning a verdict and a tag.
        """
        body: dict[str, Any] = {
            "session_id": session_id,
            "key": key,
            "content": content,
        }
        if source_reference is not None:
            body["source_reference"] = source_reference
        if human_session_context is not None:
            body["human_session_context"] = human_session_context
        data = await self._c._request("POST", "/memory/validate", json_body=body)
        return ValidationResult.model_validate(data)

    async def verify(
        self,
        *,
        session_id: str,
        entry_id: str,
        verification_evidence: str,
    ) -> ValidationResult:
        """POST /memory/verify. Promotes an existing belief to VERIFIED.

        Requires evidence (a source URL, tool result, or attested document).
        """
        body = {
            "session_id": session_id,
            "entry_id": entry_id,
            "verification_evidence": verification_evidence,
        }
        data = await self._c._request("POST", "/memory/verify", json_body=body)
        return ValidationResult.model_validate(data)

    async def list(
        self,
        *,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """GET /api/beliefs. Lists beliefs, optionally filtered by session."""
        params: dict[str, Any] = {"limit": limit}
        if session_id is not None:
            params["session_id"] = session_id
        data = await self._c._request("GET", "/api/beliefs", params=params)
        return data or []


class _AuditAPI:
    """Endpoints for inspecting the audit log."""

    def __init__(self, client: ChrysalisClient) -> None:
        self._c = client

    async def list(
        self,
        *,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[AuditRecord]:
        """GET /audit. Recent audit log entries."""
        params: dict[str, Any] = {"limit": limit}
        if session_id is not None:
            params["session_id"] = session_id
        data = await self._c._request("GET", "/audit", params=params)
        return [AuditRecord.model_validate(row) for row in (data or [])]

    async def session_summary(self, session_id: str) -> SessionSummary:
        """GET /audit/session/{session_id}/summary."""
        data = await self._c._request(
            "GET",
            f"/audit/session/{session_id}/summary",
        )
        return SessionSummary.model_validate(data)

    async def trust_score(self, agent_id: str) -> TrustScore:
        """GET /api/agent/{agent_id}/trust-score."""
        data = await self._c._request(
            "GET",
            f"/api/agent/{agent_id}/trust-score",
        )
        return TrustScore.model_validate(data)


class _AgentAPI:
    """Endpoints for the MEMOIR-governed chat agent."""

    def __init__(self, client: ChrysalisClient) -> None:
        self._c = client

    async def chat(
        self,
        *,
        message: str,
        session_id: str = "general-001",
        conversation_history: list[dict] | None = None,
    ) -> dict:
        """POST /api/agent/chat. One turn against the governed agent.

        Returns the raw response dict so callers see governance events and
        tool calls without a forced model translation.
        """
        body = {
            "session_id": session_id,
            "message": message,
            "conversation_history": conversation_history or [],
        }
        return await self._c._request("POST", "/api/agent/chat", json_body=body)


class _EventsAPI:
    """Server-Sent Events stream for governance updates."""

    def __init__(self, client: ChrysalisClient) -> None:
        self._c = client

    async def stream(
        self,
        session_id: str = "general-001",
    ) -> AsyncIterator[GovernanceEvent]:
        """GET /api/agent/events. Async iterator over governance events.

        Usage:
            async for event in client.events.stream(session_id="conv_xyz"):
                handle(event)
        """
        async with self._c._client.stream(
            "GET",
            "/api/agent/events",
            params={"session_id": session_id},
        ) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                raise ChrysalisError(resp.status_code, body.decode("utf-8", "ignore"))
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload:
                    continue
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                yield GovernanceEvent.model_validate(_normalize_event(data))


def _normalize_event(data: dict) -> dict:
    """Lift any unknown fields into the extra dict so the model stays strict."""
    known = {
        "type",
        "entry_id",
        "key",
        "epistemic_tag",
        "critique_verdict",
        "approved",
        "timestamp",
    }
    extra = {k: v for k, v in data.items() if k not in known}
    base = {k: v for k, v in data.items() if k in known}
    base["extra"] = extra
    return base
