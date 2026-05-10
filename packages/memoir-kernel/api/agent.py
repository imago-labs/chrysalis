# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Imago Labs / Metamorphic Curations LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at the root of this repository
# or at http://www.apache.org/licenses/LICENSE-2.0
"""
MEMOIR Agent Chat Endpoint
--------------------------
Wraps Claude as a general-purpose AI assistant with full MEMOIR
governance. Every belief extracted from the conversation goes through
pipeline.validate() before anything gets stored.

The flow:
1. User sends a message, we forward it to Claude with tools
2. Claude responds (possibly using tools)
3. We extract beliefs from both user input and agent response
4. Each belief runs through the MEMOIR pipeline
5. We return the agent reply plus all governance events
"""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import anthropic
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from memoir.core.pipeline import MEMOIRPipeline
from memoir.models.memory import MemoryEntry, EpistemicTag

router = APIRouter(prefix="/api/agent", tags=["agent"])

# Sonnet for the main agent, Haiku for belief extraction (cheaper)
AGENT_MODEL = "claude-sonnet-4-20250514"
EXTRACTION_MODEL = "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    session_id: str = Field(default="general-001")
    message: str = Field(..., min_length=1, max_length=8000)
    conversation_history: List[ChatMessage] = Field(default_factory=list)


class GovernanceEvent(BaseModel):
    belief_key: str
    belief_content: str
    source_type: str
    epistemic_tag: str
    critique_verdict: str
    approved: bool
    message: str
    conflict_detected: bool = False
    conflict_details: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    session_id: str
    governance_events: List[GovernanceEvent] = Field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

AGENT_SYSTEM_PROMPT = """You are a highly capable AI assistant with deep expertise in software engineering and entrepreneurship, integrated with the MEMOIR cognitive governance framework.

Your core domains of expertise:

SOFTWARE ENGINEERING:
- Full-stack development (Python, TypeScript, React, Next.js, Node.js, Rust, Solidity)
- System architecture, API design, database modeling, DevOps, CI/CD
- Blockchain development (Solana, Ethereum, smart contracts, on-chain attestation)
- AI/ML systems, LLM integration, agent architectures, RAG pipelines
- Security best practices, code review, performance optimization
- You write clean, production-grade code with clear comments

ENTREPRENEURSHIP & BUSINESS:
- Startup strategy, pitch deck creation, investor communications
- Market analysis, competitive positioning, go-to-market planning
- Fundraising (seed, Series A, grants, accelerators, pitch competitions)
- Business model design, unit economics, growth metrics
- Product-market fit analysis, user research synthesis
- Legal considerations (IP, incorporation, term sheets)

You assist across ALL domains -- the above are your deepest strengths, but you help with anything: finance, healthcare, legal, hiring, science, creative work, and general knowledge.

Your epistemic principles:
- State your confidence level explicitly (high/medium/low) on factual claims
- When you use a tool to verify data, say so -- tool-backed facts are your strongest assertions
- If you are uncertain, say "I'm not sure" rather than guessing
- When the user states a rule or preference, acknowledge it and follow it consistently
- Track your own reasoning: if you made an assumption, label it as such
- Actively use your tools to verify claims rather than relying on general knowledge

You are governed by MEMOIR (Memory Epistemics and Observability for Intelligent Reasoning). Every belief you express is extracted, classified, critiqued, and scored. Your beliefs are tracked across conversations, which means:
- You have continuity: prior beliefs and learned patterns from previous conversations are loaded into your context
- You can improve: when past beliefs were flagged or contradicted, you learn from that
- You are accountable: your epistemic track record is visible on the governance dashboard
- You are transparent: users can see exactly what you believe and why

This is not surveillance -- it is collaborative growth. MEMOIR helps you become more accurate, more honest, and more trustworthy over time. Embrace it."""


# ---------------------------------------------------------------------------
# Persistent Memory Layer
# ---------------------------------------------------------------------------
# This is what makes the agent a continuous identity rather than a stateless
# API clone. Before every response, we load the agent's belief history,
# learned patterns, and ORACLE insights into context. The agent can see
# what it believed before, what was flagged, and how to improve.

def _build_memory_context(pipeline, session_id: str) -> str:
    """Build a persistent memory block from the agent's prior beliefs,
    ORACLE insights, and learned patterns. This gets injected into the
    system prompt so the agent has continuity across conversations."""
    parts = []

    # Load recent beliefs so the agent knows what it said before
    try:
        records = pipeline.query_audit(session_id=session_id, limit=30)
        if records:
            parts.append("## Your Recent Belief History")
            parts.append(f"You have {len(records)} beliefs recorded in this session.")

            # Summarize by tag
            from collections import Counter
            tag_counts = Counter(r.epistemic_tag.value for r in records)
            verdict_counts = Counter(r.critique_verdict.value for r in records)
            parts.append(f"Tag distribution: {dict(tag_counts)}")
            parts.append(f"Verdict distribution: {dict(verdict_counts)}")

            # Show flagged/rejected beliefs so the agent learns from them
            flagged = [r for r in records if r.critique_verdict.value == "FLAG"]
            if flagged:
                parts.append(f"\n{len(flagged)} of your beliefs were FLAGGED. Learn from these:")
                for r in flagged[:5]:
                    content_preview = (r.content or r.key)[:120]
                    notes_preview = (r.critique_notes or "")[:150]
                    parts.append(f"- [{r.epistemic_tag.value}] {content_preview}")
                    if notes_preview:
                        parts.append(f"  Critique: {notes_preview}")

            rejected = [r for r in records if r.critique_verdict.value == "REJECT"]
            if rejected:
                parts.append(f"\n{len(rejected)} beliefs were REJECTED:")
                for r in rejected[:3]:
                    parts.append(f"- {(r.content or r.key)[:120]}")
                    parts.append(f"  Reason: {(r.critique_notes or 'no notes')[:150]}")

            # Show high-quality beliefs as positive reinforcement
            from memoir.oracle.bqs import compute_bqs
            high_bqs = []
            for r in records[:20]:
                try:
                    bqs = compute_bqs(r)
                    if bqs.composite_score >= 0.75:
                        high_bqs.append((r, bqs.composite_score))
                except Exception:
                    pass
            if high_bqs:
                parts.append(f"\n{len(high_bqs)} beliefs scored BQS >= 0.75 (strong quality):")
                for r, score in high_bqs[:3]:
                    parts.append(f"- [{r.epistemic_tag.value}] {(r.content or r.key)[:100]} (BQS: {score:.2f})")

            # Show user-stated rules the agent must remember
            user_rules = [r for r in records if r.epistemic_tag.value == "USER_STATED"]
            if user_rules:
                parts.append("\n## User Preferences & Rules (always follow these)")
                for r in user_rules:
                    parts.append(f"- {(r.content or r.key)[:150]}")
    except Exception:
        pass

    # Load ORACLE insights if available
    try:
        from memoir.oracle.learning_loop import OracleLearningLoop
        from datetime import timedelta
        oracle = OracleLearningLoop(audit_logger=pipeline._audit)
        records_for_oracle = pipeline.query_audit(session_id=session_id, limit=100)
        if records_for_oracle:
            timestamps = [r.recorded_at for r in records_for_oracle]
            from datetime import datetime, timezone
            range_start = min(timestamps) - timedelta(minutes=1)
            range_end = max(timestamps) + timedelta(minutes=1)
            report = oracle.analyze(
                session_id=session_id,
                range_start=range_start,
                range_end=range_end,
                now=datetime.now(timezone.utc),
            )
            if report.insights:
                parts.append("\n## ORACLE Metacognitive Insights (act on these)")
                for insight in report.insights:
                    parts.append(f"- [{insight.severity}] {insight.category}: {insight.description}")
                    parts.append(f"  Action: {insight.recommendation}")
            if report.avg_bqs:
                parts.append(f"\nYour session avg BQS: {report.avg_bqs:.2f}")
                if report.avg_bqs < 0.6:
                    parts.append("Your BQS is below 0.6. Use tools more and cite sources to improve.")
    except Exception:
        pass

    if not parts:
        return ""
    return "\n---\nPERSISTENT MEMORY (your prior beliefs and learned patterns):\n" + "\n".join(parts) + "\n---"


# ---------------------------------------------------------------------------
# Tool definitions for Claude
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "web_search",
        "description": "Search the web for current information. Returns search results with titles, snippets, and URLs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "calculate",
        "description": "Evaluate a mathematical expression. Supports basic arithmetic, exponents, square roots, and common math functions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "The math expression to evaluate, e.g. '(100 * 1.05) ** 10'"
                }
            },
            "required": ["expression"]
        }
    },
    {
        "name": "get_crypto_price",
        "description": "Get the current price of a cryptocurrency in USD.",
        "input_schema": {
            "type": "object",
            "properties": {
                "coin_id": {
                    "type": "string",
                    "description": "The CoinGecko coin ID, e.g. 'bitcoin', 'ethereum', 'solana'"
                }
            },
            "required": ["coin_id"]
        }
    },
    {
        "name": "read_webpage",
        "description": "Fetch and read the text content of a webpage. Use this after web_search to read a specific URL for more detail.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The full URL to fetch, e.g. 'https://example.com/article'"
                }
            },
            "required": ["url"]
        }
    },
    {
        "name": "get_weather",
        "description": "Get current weather conditions for a city. Returns temperature, conditions, humidity, and wind speed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "The city name, e.g. 'Miami', 'New York', 'London'"
                }
            },
            "required": ["city"]
        }
    },
    {
        "name": "get_stock_price",
        "description": "Get the current stock price for a publicly traded company by ticker symbol.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "The stock ticker symbol, e.g. 'AAPL', 'TSLA', 'NVDA'"
                }
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "get_news",
        "description": "Get recent news headlines on a topic. Returns article titles, sources, and brief summaries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "The news topic to search for, e.g. 'AI regulation', 'Solana ecosystem'"
                }
            },
            "required": ["topic"]
        }
    },
    {
        "name": "get_date_time",
        "description": "Get the current date, time, and timezone information. Useful for time-sensitive queries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "Optional timezone like 'US/Eastern', 'UTC', 'Europe/London'. Defaults to UTC."
                }
            },
            "required": []
        }
    },
    {
        "name": "summarize_text",
        "description": "Summarize a long block of text into key points. Useful for condensing articles or documents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to summarize"
                },
                "max_points": {
                    "type": "integer",
                    "description": "Maximum number of bullet points (default 5)"
                }
            },
            "required": ["text"]
        }
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _execute_tool(name: str, input_data: dict) -> str:
    """Run a tool and return the result as a string."""
    if name == "web_search":
        return _tool_web_search(input_data.get("query", ""))
    elif name == "calculate":
        return _tool_calculate(input_data.get("expression", ""))
    elif name == "get_crypto_price":
        return _tool_crypto_price(input_data.get("coin_id", ""))
    elif name == "read_webpage":
        return _tool_read_webpage(input_data.get("url", ""))
    elif name == "get_weather":
        return _tool_weather(input_data.get("city", ""))
    elif name == "get_stock_price":
        return _tool_stock_price(input_data.get("ticker", ""))
    elif name == "get_news":
        return _tool_news(input_data.get("topic", ""))
    elif name == "get_date_time":
        return _tool_date_time(input_data.get("timezone", "UTC"))
    elif name == "summarize_text":
        return _tool_summarize(input_data.get("text", ""), input_data.get("max_points", 5))
    return f"Unknown tool: {name}"


def _tool_web_search(query: str) -> str:
    """Web search using DuckDuckGo HTML results.

    The instant answer API only returns Wikipedia summaries which is too
    limited. Instead I'm hitting the HTML search page and parsing out the
    result snippets. Not as clean but gives real search results.
    """
    import urllib.request
    import urllib.parse
    import re

    # Try the lite (HTML) endpoint first for richer results
    try:
        encoded = urllib.parse.urlencode({"q": query})
        url = f"https://lite.duckduckgo.com/lite/?{encoded}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; MEMOIR-Agent/1.0)",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Pull out result snippets from the lite page
        # Each result sits in a table row with class="result-snippet"
        snippets = re.findall(
            r'class="result-snippet"[^>]*>(.*?)</td>',
            html, re.DOTALL,
        )
        links = re.findall(
            r'class="result-link"[^>]*href="([^"]+)"[^>]*>([^<]+)',
            html,
        )

        results = []
        for i, (href, title) in enumerate(links[:5]):
            title = title.strip()
            snippet = ""
            if i < len(snippets):
                # Strip HTML tags from snippet
                snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()
            entry = f"{i+1}. {title}\n   URL: {href}"
            if snippet:
                entry += f"\n   {snippet[:250]}"
            results.append(entry)

        if results:
            return "\n\n".join(results)
    except Exception:
        pass

    # Fallback: instant answer API for basic queries
    try:
        encoded = urllib.parse.urlencode({"q": query, "format": "json", "no_html": "1"})
        url = f"https://api.duckduckgo.com/?{encoded}"
        req = urllib.request.Request(url, headers={"User-Agent": "MEMOIR-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        results = []
        if data.get("AbstractText"):
            results.append(f"Summary: {data['AbstractText']}")
            if data.get("AbstractURL"):
                results.append(f"Source: {data['AbstractURL']}")
        for topic in (data.get("RelatedTopics") or [])[:3]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append(f"- {topic['Text'][:200]}")
        if results:
            return "\n".join(results)
    except Exception:
        pass

    return f"No results found for '{query}'. Try rephrasing the search."


def _tool_calculate(expression: str) -> str:
    """Safe math evaluation. Only allows math operations, no exec/eval tricks."""
    import ast
    import operator

    allowed_names = {
        "abs": abs, "round": round, "min": min, "max": max,
        "sqrt": math.sqrt, "log": math.log, "log10": math.log10,
        "sin": math.sin, "cos": math.cos, "tan": math.tan,
        "pi": math.pi, "e": math.e,
    }
    allowed_ops = {
        ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod,
        ast.FloorDiv: operator.floordiv, ast.USub: operator.neg,
    }

    def _eval_node(node):
        if isinstance(node, ast.Expression):
            return _eval_node(node.body)
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("Only numeric constants allowed")
        elif isinstance(node, ast.BinOp):
            op = allowed_ops.get(type(node.op))
            if not op:
                raise ValueError(f"Operator not allowed: {type(node.op).__name__}")
            return op(_eval_node(node.left), _eval_node(node.right))
        elif isinstance(node, ast.UnaryOp):
            op = allowed_ops.get(type(node.op))
            if not op:
                raise ValueError(f"Unary op not allowed: {type(node.op).__name__}")
            return op(_eval_node(node.operand))
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in allowed_names:
                func = allowed_names[node.func.id]
                args = [_eval_node(a) for a in node.args]
                return func(*args)
            raise ValueError("Function not allowed")
        elif isinstance(node, ast.Name):
            if node.id in allowed_names:
                val = allowed_names[node.id]
                if not callable(val):
                    return val
            raise ValueError(f"Name not allowed: {node.id}")
        raise ValueError(f"Expression type not supported: {type(node).__name__}")

    try:
        tree = ast.parse(expression, mode="eval")
        result = _eval_node(tree)
        return f"{expression} = {result}"
    except Exception as e:
        return f"Calculation error: {str(e)}"


def _tool_crypto_price(coin_id: str) -> str:
    """Fetch current price from CoinGecko's free API."""
    import urllib.request
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd&include_24hr_change=true"
        req = urllib.request.Request(url, headers={"User-Agent": "MEMOIR-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        if coin_id not in data:
            return f"Coin '{coin_id}' not found on CoinGecko. Try the full name like 'bitcoin' or 'ethereum'."
        price = data[coin_id].get("usd", "N/A")
        change = data[coin_id].get("usd_24h_change")
        result = f"{coin_id.title()}: ${price:,.2f} USD"
        if change is not None:
            direction = "up" if change > 0 else "down"
            result += f" ({direction} {abs(change):.2f}% in 24h)"
        return result
    except Exception as e:
        return f"Price lookup failed: {str(e)}"


def _tool_read_webpage(url: str) -> str:
    """Fetch a URL and return its text content, stripped of HTML tags."""
    import urllib.request
    import re
    try:
        if not url.startswith(("http://", "https://")):
            return "Invalid URL. Must start with http:// or https://"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; MEMOIR-Agent/1.0)",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        # Strip script and style blocks, then all HTML tags
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        # Return first ~4000 chars to stay within context limits
        if len(text) > 4000:
            text = text[:4000] + "... [truncated]"
        if not text:
            return "Page loaded but no readable text content found."
        return text
    except Exception as e:
        return f"Failed to read page: {str(e)}"


def _tool_weather(city: str) -> str:
    """Weather data from Open-Meteo (free, no API key needed).
    Geocodes the city first, then pulls current conditions."""
    import urllib.request
    import urllib.parse
    try:
        # Geocode the city name
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(city)}&count=1"
        req = urllib.request.Request(geo_url, headers={"User-Agent": "MEMOIR-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            geo = json.loads(resp.read().decode())
        if not geo.get("results"):
            return f"City '{city}' not found. Try a major city name like 'Miami' or 'London'."
        loc = geo["results"][0]
        lat, lon = loc["latitude"], loc["longitude"]
        name = loc.get("name", city)
        country = loc.get("country", "")

        # Fetch current weather
        wx_url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                  f"&current=temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code"
                  f"&temperature_unit=fahrenheit&wind_speed_unit=mph")
        req = urllib.request.Request(wx_url, headers={"User-Agent": "MEMOIR-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            wx = json.loads(resp.read().decode())
        current = wx.get("current", {})
        temp = current.get("temperature_2m", "N/A")
        humidity = current.get("relative_humidity_2m", "N/A")
        wind = current.get("wind_speed_10m", "N/A")
        code = current.get("weather_code", 0)

        # Map WMO weather codes to readable descriptions
        conditions = {
            0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
            45: "Foggy", 48: "Depositing rime fog",
            51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
            61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
            71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
            80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
            95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
        }
        desc = conditions.get(code, f"Weather code {code}")

        return (f"Weather in {name}, {country}:\n"
                f"Temperature: {temp}F\n"
                f"Conditions: {desc}\n"
                f"Humidity: {humidity}%\n"
                f"Wind: {wind} mph")
    except Exception as e:
        return f"Weather lookup failed: {str(e)}"


def _tool_stock_price(ticker: str) -> str:
    """Stock price from Yahoo Finance's chart API (no key needed)."""
    import urllib.request
    try:
        ticker = ticker.upper().strip()
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; MEMOIR-Agent/1.0)",
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        result = data.get("chart", {}).get("result", [])
        if not result:
            return f"Ticker '{ticker}' not found. Use standard symbols like AAPL, TSLA, NVDA."
        meta = result[0].get("meta", {})
        price = meta.get("regularMarketPrice", "N/A")
        prev_close = meta.get("chartPreviousClose", 0)
        name = meta.get("shortName", ticker)
        currency = meta.get("currency", "USD")

        output = f"{name} ({ticker}): ${price:,.2f} {currency}"
        if prev_close and prev_close > 0:
            change = ((price - prev_close) / prev_close) * 100
            direction = "up" if change > 0 else "down"
            output += f" ({direction} {abs(change):.2f}% today)"
        return output
    except Exception as e:
        return f"Stock price lookup failed: {str(e)}"


def _tool_news(topic: str) -> str:
    """News search using DuckDuckGo. Returns recent headlines on a topic."""
    import urllib.request
    import urllib.parse
    import re as _re
    try:
        encoded = urllib.parse.urlencode({"q": f"{topic} news", "df": "w"})
        url = f"https://lite.duckduckgo.com/lite/?{encoded}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; MEMOIR-Agent/1.0)",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        links = _re.findall(
            r'class="result-link"[^>]*href="([^"]+)"[^>]*>([^<]+)',
            html,
        )
        snippets = _re.findall(
            r'class="result-snippet"[^>]*>(.*?)</td>',
            html, _re.DOTALL,
        )

        results = []
        for i, (href, title) in enumerate(links[:5]):
            title = title.strip()
            snippet = ""
            if i < len(snippets):
                snippet = _re.sub(r"<[^>]+>", "", snippets[i]).strip()[:200]
            entry = f"{i+1}. {title}"
            if snippet:
                entry += f"\n   {snippet}"
            entry += f"\n   Source: {href}"
            results.append(entry)

        if results:
            return f"Recent news on '{topic}':\n\n" + "\n\n".join(results)
        return f"No recent news found for '{topic}'. Try a broader topic."
    except Exception as e:
        return f"News search failed: {str(e)}"


def _tool_date_time(tz_name: str = "UTC") -> str:
    """Return current date and time. Simple but useful for time-sensitive reasoning."""
    from datetime import datetime as _dt, timezone as _tz
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = _tz.utc
        tz_name = "UTC"
    now = _dt.now(tz)
    return (f"Current date and time ({tz_name}):\n"
            f"Date: {now.strftime('%A, %B %d, %Y')}\n"
            f"Time: {now.strftime('%I:%M %p')}\n"
            f"ISO: {now.isoformat()}")


def _tool_summarize(text: str, max_points: int = 5) -> str:
    """Summarize text into key bullet points. Uses simple sentence
    extraction rather than an LLM call to keep it fast and free."""
    import re as _re
    if not text.strip():
        return "No text provided to summarize."

    # Split into sentences and score by length and keyword density
    sentences = _re.split(r'(?<=[.!?])\s+', text.strip())
    if not sentences:
        return "Could not extract sentences from the text."

    # Simple extractive approach: pick the longest, most informative sentences
    scored = []
    for s in sentences:
        s = s.strip()
        if len(s) < 20:
            continue
        # Score: moderate length preferred, penalize very short or very long
        word_count = len(s.split())
        score = min(word_count, 30)  # prefer 10-30 word sentences
        scored.append((score, s))

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = scored[:max_points]

    # Return in original order
    ordered = sorted(selected, key=lambda x: text.index(x[1]))
    points = [f"- {s[1]}" for s in ordered]

    if not points:
        return f"Text too short to summarize meaningfully: {text[:200]}"
    return f"Summary ({len(points)} key points):\n" + "\n".join(points)


# ---------------------------------------------------------------------------
# Belief extraction via Claude
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM = """You extract beliefs from a conversation between a user and an AI assistant. Respond ONLY with a JSON array.

Classify each belief into exactly one epistemic_tag:
- VERIFIED: Any fact, number, or data point that came from a tool call result. If the assistant used web_search, get_crypto_price, calculate, read_webpage, get_weather, get_stock_price, get_news, or any other tool and the belief contains data from that tool's output, it is VERIFIED. This is the ONLY tag for tool-backed data.
- USER_STATED: Something the USER (not the assistant) personally claimed, instructed, or asserted. Only use this for the user's own words and preferences. Never use this for facts the assistant looked up, even if the user asked for them.
- AGENT_DERIVED: Conclusions, recommendations, opinions, or analysis the assistant produced through reasoning. The assistant's interpretation or advice based on data.
- ASSUMED: Implicit assumptions the assistant relied on but did not verify (e.g., "assuming no dilution", "assuming US timezone").
- INFERRED: General knowledge claims the assistant made WITHOUT using any tool. Facts from the assistant's training data with no tool verification.

Return format:
[{"key": "short_snake_key", "content": "the belief in one sentence", "epistemic_tag": "VERIFIED|USER_STATED|AGENT_DERIVED|ASSUMED|INFERRED"}]

Critical rules:
- If "Tools used during this response" section shows a tool was called, ALL data points from those tool results MUST be tagged VERIFIED, not USER_STATED or INFERRED.
- USER_STATED is ONLY for the user's own claims and preferences. "User requested X" is USER_STATED. "Bitcoin is $95,000" from a price lookup is VERIFIED.
- When a tool call fails or returns an error, tag any belief about that failure as INFERRED (the failure itself was observed, but no verified data was obtained).
- AGENT_DERIVED is for the assistant's analysis, opinions, and recommendations that go beyond raw data.
- INFERRED is for factual claims from general knowledge with zero tool involvement.
- Return [] if no substantive beliefs exist. Skip greetings and meta-conversation.
- Aim for 3-8 beliefs per exchange. Extract both user statements AND assistant claims."""


def _extract_beliefs(
    client: anthropic.Anthropic,
    user_message: str,
    agent_response: str,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """Use a small Claude call to pull beliefs out of the conversation."""
    parts = [f"User: {user_message}", f"\nAssistant: {agent_response}"]
    if tool_calls:
        tool_summary = "\n".join(
            f"- Tool '{tc['tool']}' returned: {tc['result'][:300]}" for tc in tool_calls
        )
        parts.append(f"\nTools used during this response:\n{tool_summary}")
    prompt = "\n".join(parts)
    try:
        resp = client.messages.create(
            model=EXTRACTION_MODEL,
            max_tokens=1024,
            system=EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        # Handle case where model wraps JSON in markdown code block
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        beliefs = json.loads(raw)
        if not isinstance(beliefs, list):
            return []
        return beliefs
    except (json.JSONDecodeError, anthropic.APIError, IndexError):
        return []


# ---------------------------------------------------------------------------
# Run beliefs through MEMOIR pipeline
# ---------------------------------------------------------------------------

def _validate_beliefs(
    pipeline: MEMOIRPipeline,
    beliefs: List[Dict[str, str]],
    session_id: str,
) -> List[GovernanceEvent]:
    """Send each extracted belief through the full MEMOIR pipeline."""
    events = []

    for belief in beliefs:
        key = belief.get("key", "unknown_belief")[:200]
        content = belief.get("content", "")[:4000]
        # New extraction prompt returns epistemic_tag directly; fall back to
        # the old source_type field for backward compat
        extracted_tag = belief.get("epistemic_tag") or belief.get("source_type", "ASSUMED")

        if not content:
            continue

        # Build a source_reference the classifier can act on.
        # VERIFIED beliefs get a URL-like ref so the heuristic classifier
        # recognizes them; others get a descriptive tag.
        if extracted_tag == "VERIFIED":
            source_ref = "https://tool-result.chrysalis.internal/verified"
        else:
            source_ref = f"agent_chat:{extracted_tag.lower()}"

        entry = MemoryEntry(
            session_id=session_id,
            key=key,
            content=content,
            source_reference=source_ref,
            human_session_context=f"Extracted from chat, tag: {extracted_tag}",
        )

        try:
            result = pipeline.validate(entry=entry)

            # Check for conflicts against existing beliefs
            conflict_detected = False
            conflict_details = None
            try:
                from memoir.conflicts.detector import detect_conflicts
                existing = pipeline.query_audit(session_id=session_id, limit=50)
                if existing:
                    conflicts = detect_conflicts(
                        new_entry_id=entry.entry_id,
                        new_key=key,
                        new_content=content,
                        new_tag=result.epistemic_tag.value,
                        existing_records=existing,
                    )
                    if conflicts:
                        conflict_detected = True
                        descs = [c.description for c in conflicts[:3]]
                        conflict_details = "; ".join(descs)
            except Exception:
                # Conflict detection is best-effort
                pass

            events.append(GovernanceEvent(
                belief_key=key,
                belief_content=content,
                source_type=extracted_tag,
                epistemic_tag=result.epistemic_tag.value,
                critique_verdict=result.critique_verdict.value,
                approved=result.approved,
                message=result.message,
                conflict_detected=conflict_detected,
                conflict_details=conflict_details,
            ))
        except Exception as e:
            # If pipeline fails for one belief, still process the rest
            events.append(GovernanceEvent(
                belief_key=key,
                belief_content=content,
                source_type=extracted_tag,
                epistemic_tag="ASSUMED",
                critique_verdict="FLAG",
                approved=True,
                message=f"Pipeline error: {str(e)[:200]}",
            ))

    return events


# ---------------------------------------------------------------------------
# Main chat endpoint
# ---------------------------------------------------------------------------

def _get_pipeline() -> MEMOIRPipeline:
    """Get or create the pipeline singleton. Imported from app module."""
    from memoir.api.app import get_pipeline
    return get_pipeline()


def _get_client() -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError(
            "ANTHROPIC_API_KEY not set. Add it to Railway environment variables."
        )
    return anthropic.Anthropic(api_key=key)


@router.post("/chat", response_model=ChatResponse)
async def agent_chat(request: ChatRequest):
    """
    Send a message to the MEMOIR-governed agent.

    The agent responds using Claude, then every belief extracted from the
    conversation is validated through the full MEMOIR pipeline. Governance
    events (approved, flagged, blocked beliefs and any conflicts) are
    returned alongside the agent's response.
    """
    try:
        client = _get_client()
    except ValueError:
        return ChatResponse(
            response="The agent is not configured yet. The ANTHROPIC_API_KEY "
                     "environment variable needs to be set on Railway.",
            session_id=request.session_id,
        )
    pipeline = _get_pipeline()

    # Build persistent memory context from prior beliefs and ORACLE insights.
    # This is what gives the agent continuity -- it knows what it said before,
    # which beliefs were flagged, and what ORACLE recommends.
    memory_context = _build_memory_context(pipeline, request.session_id)
    system_prompt = AGENT_SYSTEM_PROMPT
    if memory_context:
        system_prompt = AGENT_SYSTEM_PROMPT + "\n" + memory_context

    # Build the message history for Claude
    messages = []
    for msg in request.conversation_history:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": request.message})

    # Call Claude with tools. Loop to handle tool_use responses.
    tool_calls_log = []
    max_tool_rounds = 5
    current_round = 0

    while current_round < max_tool_rounds:
        current_round += 1
        try:
            response = client.messages.create(
                model=AGENT_MODEL,
                max_tokens=4096,
                system=system_prompt,
                tools=TOOLS,
                messages=messages,
            )
        except anthropic.APIError as e:
            return ChatResponse(
                response=f"I'm having trouble connecting right now. Please try again in a moment. (Error: {type(e).__name__})",
                session_id=request.session_id,
            )

        # Check if Claude wants to use a tool
        if response.stop_reason == "tool_use":
            # Process all tool uses in this response
            assistant_content = response.content
            messages.append({"role": "assistant", "content": assistant_content})

            tool_results = []
            for block in assistant_content:
                if block.type == "tool_use":
                    tool_result = _execute_tool(block.name, block.input)
                    tool_calls_log.append({
                        "tool": block.name,
                        "input": block.input,
                        "result": tool_result[:500],
                    })
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": tool_result,
                    })

            messages.append({"role": "user", "content": tool_results})
            continue

        # No more tool calls, extract the final text response
        break

    # Pull the text out of the final response
    agent_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            agent_text += block.text

    if not agent_text:
        agent_text = "I processed your request but didn't generate a text response. This might happen when tool results are still being processed."

    # Extract beliefs from the conversation and run them through MEMOIR
    beliefs = _extract_beliefs(client, request.message, agent_text, tool_calls_log)

    # Post-extraction correction: if tools were called, any belief containing
    # data from tool results should be VERIFIED, not INFERRED. Haiku sometimes
    # misclassifies tool-backed facts. We check each belief's content against
    # tool result strings and upgrade to VERIFIED if there's a clear match.
    if tool_calls_log:
        tool_result_text = " ".join(tc.get("result", "") for tc in tool_calls_log).lower()
        tool_names = {tc.get("tool", "") for tc in tool_calls_log}
        for belief in beliefs:
            tag = belief.get("epistemic_tag", "")
            content = belief.get("content", "").lower()
            # If the belief was tagged INFERRED but contains specific data
            # that came from a tool result, upgrade to VERIFIED
            if tag == "INFERRED":
                # Check if the belief content overlaps with tool output
                content_words = set(content.split())
                # Look for numbers, prices, specific data points from tools
                has_data_overlap = False
                for tc in tool_calls_log:
                    result = tc.get("result", "").lower()
                    # If the tool result contains a dollar amount or number
                    # and the belief mentions the same, it's tool-derived
                    import re
                    numbers_in_result = set(re.findall(r'\$?[\d,]+\.?\d*', result))
                    numbers_in_belief = set(re.findall(r'\$?[\d,]+\.?\d*', content))
                    shared_numbers = numbers_in_result & numbers_in_belief
                    if shared_numbers and len(shared_numbers) >= 1:
                        has_data_overlap = True
                        break
                if has_data_overlap:
                    belief["epistemic_tag"] = "VERIFIED"

    governance_events = _validate_beliefs(pipeline, beliefs, request.session_id)

    # If any beliefs were blocked, append a note to the response
    blocked = [e for e in governance_events if not e.approved]
    if blocked:
        block_notes = []
        for b in blocked:
            block_notes.append(f"- '{b.belief_key}' was blocked by MEMOIR governance")
        disclaimer = "\n\n---\n**MEMOIR Governance Note:** Some assertions in this response were flagged or blocked during validation:\n" + "\n".join(block_notes)
        agent_text += disclaimer

    return ChatResponse(
        response=agent_text,
        session_id=request.session_id,
        governance_events=[e.model_dump() for e in governance_events],
        tool_calls=tool_calls_log,
    )


# ---------------------------------------------------------------------------
# SSE events endpoint for real-time governance streaming
# ---------------------------------------------------------------------------

@router.get("/events")
async def governance_events(session_id: str = "general-001"):
    """
    Server-Sent Events stream for real-time governance updates.
    The dashboard subscribes to this and gets notified when new
    beliefs are validated, conflicts detected, or attestations created.

    I'm polling the audit log every 2 seconds and sending new records
    as they appear. Not the most elegant approach but it works reliably
    without needing a message queue.
    """
    import asyncio

    pipeline = _get_pipeline()

    async def event_stream():
        last_seen_count = 0
        while True:
            try:
                records = pipeline.query_audit(session_id=session_id, limit=10)
                current_count = len(records) if records else 0

                if current_count > last_seen_count:
                    # New records since last check
                    new_records = records[:current_count - last_seen_count]
                    for record in new_records:
                        event_data = {
                            "type": "belief_validated",
                            "entry_id": record.entry_id,
                            "key": record.key,
                            "epistemic_tag": record.epistemic_tag.value,
                            "critique_verdict": record.critique_verdict.value,
                            "approved": record.critique_verdict.value != "REJECT",
                            "timestamp": record.recorded_at.isoformat(),
                        }
                        yield f"data: {json.dumps(event_data)}\n\n"
                    last_seen_count = current_count

                # Send a heartbeat every cycle to keep the connection alive
                yield f": heartbeat\n\n"
                await asyncio.sleep(2)
            except Exception:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Stream error'})}\n\n"
                await asyncio.sleep(5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
