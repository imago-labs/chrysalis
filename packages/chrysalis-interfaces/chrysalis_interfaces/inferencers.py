# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at the root of this repository
# or at http://www.apache.org/licenses/LICENSE-2.0
"""
Reference Inferencer implementations for local model backends.

Two reference adapters ship with the open kernel:

  - OllamaInferencer: targets a local Ollama server (default :11434). Best
    for developer machines and small-scale inference. No GPU required for
    most quantized models.

  - VLLMInferencer: targets a vLLM server speaking the OpenAI-compatible
    chat completions API (default :8000). Best for production GPU
    deployments and serving open-weights models at scale.

Both adapters use httpx so they have a single light dependency and no
vendor SDKs. They are reference implementations: complete enough to run
real agent loops, simple enough to read in one sitting.

Cloud backends (Anthropic, OpenAI, Bedrock) live in the closed Chrysalis
platform alongside the production critic, monitors, and attesters.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from chrysalis_interfaces.protocols import (
    InferenceMessage,
    InferenceResponse,
    Inferencer,
    ToolCall,
    ToolSpec,
)

log = logging.getLogger(__name__)

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]


def _require_httpx() -> None:
    if not _HTTPX_AVAILABLE:
        raise RuntimeError(
            "The reference Inferencer adapters need httpx. Install with "
            "'pip install httpx' or 'pip install chrysalis-kernel[inference]'."
        )


# ---------------------------------------------------------------------------
# OpenAI-style tool-call helpers, shared by vLLM and any other OAI-compatible
# server. Ollama also supports tool calls in this exact shape for the
# /api/chat endpoint.
# ---------------------------------------------------------------------------


def _tools_to_openai_schema(tools: list[ToolSpec] | None) -> list[dict] | None:
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


def _messages_to_openai_schema(
    messages: list[InferenceMessage],
    system: str | None,
) -> list[dict]:
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages:
        entry: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.name:
            entry["name"] = m.name
        if m.tool_call_id:
            entry["tool_call_id"] = m.tool_call_id
        out.append(entry)
    return out


def _parse_openai_tool_calls(raw_calls: list[dict] | None) -> list[ToolCall]:
    if not raw_calls:
        return []
    parsed: list[ToolCall] = []
    for c in raw_calls:
        fn = c.get("function", {}) if isinstance(c, dict) else {}
        args_raw = fn.get("arguments", "{}")
        if isinstance(args_raw, str):
            try:
                args = json.loads(args_raw)
            except json.JSONDecodeError:
                args = {"_raw": args_raw}
        else:
            args = dict(args_raw or {})
        parsed.append(
            ToolCall(
                id=str(c.get("id") or f"call_{uuid.uuid4().hex[:12]}"),
                name=str(fn.get("name", "")),
                arguments=args,
            )
        )
    return parsed


# ---------------------------------------------------------------------------
# OllamaInferencer
# ---------------------------------------------------------------------------


class OllamaInferencer(Inferencer):
    """Reference Inferencer backed by a local Ollama server.

    Uses the /api/chat endpoint, which supports a system prompt, message
    history, and OpenAI-style tools (Ollama >= 0.3.0).

    Quick start:
        ollama serve
        ollama pull llama3.1:8b
        inf = OllamaInferencer(model="llama3.1:8b")

    Args:
        model: Ollama model tag, for example "llama3.1:8b" or "qwen2.5:14b".
        base_url: Ollama server URL. Defaults to http://localhost:11434.
        timeout: Per-request timeout in seconds. Local inference can be slow
            on CPU, so the default is generous.
        client: Optional preconfigured httpx.AsyncClient. The Inferencer does
            not own its lifecycle when provided.
    """

    def __init__(
        self,
        model: str = "llama3.1:8b",
        base_url: str = "http://localhost:11434",
        timeout: float = 120.0,
        client: Any = None,
    ) -> None:
        _require_httpx()
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    async def _get_client(self) -> Any:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def complete(
        self,
        messages: list[InferenceMessage],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        stop_sequences: list[str] | None = None,
    ) -> InferenceResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": _messages_to_openai_schema(messages, system),
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if stop_sequences:
            payload["options"]["stop"] = stop_sequences
        oai_tools = _tools_to_openai_schema(tools)
        if oai_tools:
            payload["tools"] = oai_tools

        client = await self._get_client()
        resp = await client.post(f"{self.base_url}/api/chat", json=payload)
        resp.raise_for_status()
        body = resp.json()

        msg = body.get("message", {}) or {}
        text = msg.get("content", "") or ""
        tool_calls = _parse_openai_tool_calls(msg.get("tool_calls"))

        if tool_calls:
            stop_reason = "tool_use"
        elif body.get("done_reason") == "length":
            stop_reason = "max_tokens"
        else:
            stop_reason = "end_turn"

        usage = {
            "input_tokens": body.get("prompt_eval_count", 0),
            "output_tokens": body.get("eval_count", 0),
        }

        return InferenceResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            model=body.get("model", self.model),
            usage=usage,
            raw=body,
        )


# ---------------------------------------------------------------------------
# VLLMInferencer
# ---------------------------------------------------------------------------


class VLLMInferencer(Inferencer):
    """Reference Inferencer backed by a vLLM OpenAI-compatible server.

    vLLM serves open-weights models with PagedAttention and continuous
    batching. The official server exposes the OpenAI chat completions
    schema at /v1/chat/completions.

    Quick start:
        vllm serve meta-llama/Llama-3.1-8B-Instruct
        inf = VLLMInferencer(model="meta-llama/Llama-3.1-8B-Instruct")

    Args:
        model: Model name the vLLM server is hosting.
        base_url: vLLM server URL. Defaults to http://localhost:8000.
        api_key: Optional bearer token if the server is gated. vLLM accepts
            any non-empty value when --api-key is set; "EMPTY" is the
            documented sentinel.
        timeout: Per-request timeout in seconds.
        client: Optional preconfigured httpx.AsyncClient.
    """

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:8000",
        api_key: str | None = None,
        timeout: float = 120.0,
        client: Any = None,
    ) -> None:
        _require_httpx()
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    async def _get_client(self) -> Any:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def complete(
        self,
        messages: list[InferenceMessage],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        stop_sequences: list[str] | None = None,
    ) -> InferenceResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": _messages_to_openai_schema(messages, system),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if stop_sequences:
            payload["stop"] = stop_sequences
        oai_tools = _tools_to_openai_schema(tools)
        if oai_tools:
            payload["tools"] = oai_tools

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        client = await self._get_client()
        resp = await client.post(
            f"{self.base_url}/v1/chat/completions",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        body = resp.json()

        choices = body.get("choices") or []
        if not choices:
            return InferenceResponse(
                text="",
                stop_reason="end_turn",
                model=body.get("model", self.model),
                raw=body,
            )

        choice = choices[0]
        msg = choice.get("message", {}) or {}
        text = msg.get("content") or ""
        tool_calls = _parse_openai_tool_calls(msg.get("tool_calls"))

        raw_stop = choice.get("finish_reason") or "stop"
        if tool_calls or raw_stop == "tool_calls":
            stop_reason = "tool_use"
        elif raw_stop == "length":
            stop_reason = "max_tokens"
        else:
            stop_reason = "end_turn"

        usage_raw = body.get("usage", {}) or {}
        usage = {
            "input_tokens": usage_raw.get("prompt_tokens", 0),
            "output_tokens": usage_raw.get("completion_tokens", 0),
        }

        return InferenceResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            model=body.get("model", self.model),
            usage=usage,
            raw=body,
        )


__all__ = ["OllamaInferencer", "VLLMInferencer"]
