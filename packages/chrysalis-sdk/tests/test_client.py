# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
"""Tests for the chrysalis-sdk async client."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from chrysalis_sdk import ChrysalisClient, ChrysalisError


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Transport helpers
# ---------------------------------------------------------------------------


def _make_client(handler) -> ChrysalisClient:
    """Build a ChrysalisClient backed by an httpx MockTransport."""
    transport = httpx.MockTransport(handler)
    httpx_client = httpx.AsyncClient(
        base_url="https://api.test",
        transport=transport,
        headers={"Accept": "application/json", "X-API-Key": "test-key"},
    )
    return ChrysalisClient(
        base_url="https://api.test",
        api_key="test-key",
        client=httpx_client,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok"})

    async with _make_client(handler) as client:
        result = await client.health()
        assert result == {"status": "ok"}


@pytest.mark.asyncio
async def test_validate_belief_sends_api_key_header() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "entry_id": "e_1",
                "approved": True,
                "epistemic_tag": "ASSUMED",
                "critique_verdict": "PASS",
                "message": "ok",
            },
        )

    async with _make_client(handler) as client:
        result = await client.beliefs.validate(
            session_id="s_1",
            key="user_pref",
            content="prefers brevity",
            source_reference="turn 14",
        )

    assert result.approved is True
    assert result.epistemic_tag == "ASSUMED"
    assert captured["headers"].get("x-api-key") == "test-key"
    assert captured["body"]["session_id"] == "s_1"
    assert captured["body"]["key"] == "user_pref"
    assert captured["body"]["source_reference"] == "turn 14"


@pytest.mark.asyncio
async def test_audit_list_parses_records() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/audit"
        assert request.url.params["session_id"] == "s_1"
        return httpx.Response(
            200,
            json=[
                {
                    "entry_id": "e_1",
                    "session_id": "s_1",
                    "key": "k",
                    "content": "c",
                    "epistemic_tag": "VERIFIED",
                    "critique_verdict": "PASS",
                    "recorded_at": _now_iso(),
                }
            ],
        )

    async with _make_client(handler) as client:
        records = await client.audit.list(session_id="s_1", limit=10)
        assert len(records) == 1
        assert records[0].epistemic_tag == "VERIFIED"


@pytest.mark.asyncio
async def test_error_response_raises_chrysalis_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "missing api key"})

    async with _make_client(handler) as client:
        with pytest.raises(ChrysalisError) as exc:
            await client.beliefs.list()
        assert exc.value.status_code == 403
        assert "missing api key" in str(exc.value)


@pytest.mark.asyncio
async def test_event_stream_parses_sse_lines() -> None:
    sse_body = (
        ": heartbeat\n\n"
        "data: " + json.dumps({
            "type": "belief_validated",
            "entry_id": "e_1",
            "key": "k",
            "epistemic_tag": "ASSUMED",
            "critique_verdict": "PASS",
            "approved": True,
            "timestamp": _now_iso(),
            "custom_field": "stash this in extra",
        }) + "\n\n"
        "data: " + json.dumps({"type": "heartbeat"}) + "\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/agent/events"
        return httpx.Response(
            200,
            content=sse_body.encode(),
            headers={"content-type": "text/event-stream"},
        )

    async with _make_client(handler) as client:
        events = []
        async for ev in client.events.stream(session_id="s_1"):
            events.append(ev)
            if len(events) == 2:
                break

    assert events[0].type == "belief_validated"
    assert events[0].key == "k"
    assert events[0].extra.get("custom_field") == "stash this in extra"
    assert events[1].type == "heartbeat"
