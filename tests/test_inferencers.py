# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
"""Tests for the Inferencer Protocol and reference adapters.

The reference adapters (Ollama, vLLM) talk to HTTP servers, so the tests
inject a fake httpx.AsyncClient and assert payload shape and response
parsing. No network is required.
"""

from __future__ import annotations

import json

import pytest

from chrysalis_interfaces import (
    InferenceMessage,
    Inferencer,
    OllamaInferencer,
    ToolSpec,
    VLLMInferencer,
)


# ---------------------------------------------------------------------------
# Fake httpx client
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.last_url: str | None = None
        self.last_json: dict | None = None
        self.last_headers: dict | None = None
        self.closed = False

    async def post(self, url, *, json=None, headers=None):
        self.last_url = url
        self.last_json = json
        self.last_headers = headers or {}
        return _FakeResponse(self._payload)

    async def aclose(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_ollama_satisfies_inferencer_protocol() -> None:
    inf = OllamaInferencer(model="llama3.1:8b", client=_FakeClient({}))
    assert isinstance(inf, Inferencer)


def test_vllm_satisfies_inferencer_protocol() -> None:
    inf = VLLMInferencer(model="meta-llama/Llama-3.1-8B-Instruct", client=_FakeClient({}))
    assert isinstance(inf, Inferencer)


# ---------------------------------------------------------------------------
# OllamaInferencer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ollama_text_completion() -> None:
    fake = _FakeClient(
        {
            "model": "llama3.1:8b",
            "message": {"role": "assistant", "content": "Hello world."},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 12,
            "eval_count": 4,
        }
    )
    inf = OllamaInferencer(model="llama3.1:8b", client=fake)

    resp = await inf.complete(
        [InferenceMessage(role="user", content="hi")],
        system="be helpful",
        max_tokens=128,
        temperature=0.2,
    )

    assert resp.text == "Hello world."
    assert resp.stop_reason == "end_turn"
    assert resp.tool_calls == []
    assert resp.usage == {"input_tokens": 12, "output_tokens": 4}
    assert resp.model == "llama3.1:8b"

    # Outgoing payload shape.
    assert fake.last_url.endswith("/api/chat")
    body = fake.last_json
    assert body["model"] == "llama3.1:8b"
    assert body["stream"] is False
    assert body["options"]["temperature"] == 0.2
    assert body["options"]["num_predict"] == 128
    # System prompt is prepended to the messages array.
    assert body["messages"][0] == {"role": "system", "content": "be helpful"}
    assert body["messages"][1] == {"role": "user", "content": "hi"}


@pytest.mark.asyncio
async def test_ollama_tool_call_parsing() -> None:
    fake = _FakeClient(
        {
            "model": "llama3.1:8b",
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "function": {
                            "name": "search",
                            "arguments": json.dumps({"q": "cats"}),
                        },
                    }
                ],
            },
            "done": True,
        }
    )
    inf = OllamaInferencer(model="llama3.1:8b", client=fake)

    tools = [
        ToolSpec(
            name="search",
            description="Web search",
            parameters={
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        )
    ]
    resp = await inf.complete(
        [InferenceMessage(role="user", content="look up cats")],
        tools=tools,
    )

    assert resp.stop_reason == "tool_use"
    assert len(resp.tool_calls) == 1
    call = resp.tool_calls[0]
    assert call.id == "call_abc"
    assert call.name == "search"
    assert call.arguments == {"q": "cats"}

    # Tools were sent in OpenAI function-call schema.
    sent_tools = fake.last_json["tools"]
    assert sent_tools[0]["type"] == "function"
    assert sent_tools[0]["function"]["name"] == "search"


# ---------------------------------------------------------------------------
# VLLMInferencer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vllm_text_completion_with_api_key() -> None:
    fake = _FakeClient(
        {
            "model": "meta-llama/Llama-3.1-8B-Instruct",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Hi from vLLM."},
                }
            ],
            "usage": {"prompt_tokens": 8, "completion_tokens": 5},
        }
    )
    inf = VLLMInferencer(
        model="meta-llama/Llama-3.1-8B-Instruct",
        api_key="EMPTY",
        client=fake,
    )

    resp = await inf.complete(
        [InferenceMessage(role="user", content="hi")],
        max_tokens=64,
    )

    assert resp.text == "Hi from vLLM."
    assert resp.stop_reason == "end_turn"
    assert resp.usage == {"input_tokens": 8, "output_tokens": 5}

    assert fake.last_url.endswith("/v1/chat/completions")
    assert fake.last_headers.get("Authorization") == "Bearer EMPTY"
    assert fake.last_json["max_tokens"] == 64


@pytest.mark.asyncio
async def test_vllm_finish_reason_length_maps_to_max_tokens() -> None:
    fake = _FakeClient(
        {
            "model": "x",
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"role": "assistant", "content": "truncated..."},
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 64},
        }
    )
    inf = VLLMInferencer(model="x", client=fake)
    resp = await inf.complete([InferenceMessage(role="user", content="...")])
    assert resp.stop_reason == "max_tokens"


@pytest.mark.asyncio
async def test_vllm_malformed_tool_arguments_dont_crash() -> None:
    fake = _FakeClient(
        {
            "model": "x",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "1",
                                "function": {"name": "search", "arguments": "not-json"},
                            }
                        ],
                    },
                }
            ],
            "usage": {},
        }
    )
    inf = VLLMInferencer(model="x", client=fake)
    resp = await inf.complete([InferenceMessage(role="user", content="go")])
    assert resp.stop_reason == "tool_use"
    assert resp.tool_calls[0].arguments == {"_raw": "not-json"}
