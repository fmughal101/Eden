"""
Ticker research
===============
Pulls fundamentals + recent news from yfinance, asks Claude to produce a
balanced bull/bear thesis with a BUY/HOLD/SELL rating, and uses the built-in
web_search tool to verify recent material events. Results are cached per
(symbol, day) for 24h to limit API spend.

Required env var: ANTHROPIC_API_KEY.
"""

import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any

import anthropic
import yfinance as yf

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Whitelisted yfinance .info keys. Skip everything else — keeps the prompt
# small and avoids leaking junk fields that may change between yfinance releases.
_FUNDAMENTAL_KEYS = (
    "marketCap", "trailingPE", "forwardPE", "profitMargins",
    "returnOnEquity", "debtToEquity", "revenueGrowth", "dividendYield",
    "beta", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
    "sector", "industry", "longBusinessSummary",
)

_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-]+$")

# System prompt — static, eligible for prompt caching.
SYSTEM_PROMPT = """You are an equity research assistant. Given a ticker's
fundamentals and recent news, produce a balanced bull/bear analysis.

Use the web_search tool to verify recent material events (earnings releases,
guidance, regulatory actions, M&A). Search when fundamentals look stale or
when news headlines hint at unresolved questions; skip search when the data
in the user message is already sufficient.

Be conservative: prefer "HOLD" when evidence is mixed. Cite at least 2
sources for any "BUY" or "SELL" rating. Always include the disclaimer
"Not financial advice." in the summary.

Return only the structured object the schema requires — no preamble, no
trailing prose. Bull and bear lists should each contain 3-5 short bullets
(≤ 20 words each)."""

# JSON schema for the response. Strict — no extra keys, all required.
_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "rating":     {"type": "string", "enum": ["BUY", "HOLD", "SELL"]},
        "confidence": {"type": "integer"},
        "summary":    {"type": "string"},
        "bull":       {"type": "array", "items": {"type": "string"}},
        "bear":       {"type": "array", "items": {"type": "string"}},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url":   {"type": "string"},
                },
                "required": ["title", "url"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["rating", "confidence", "summary", "bull", "bear", "sources"],
    "additionalProperties": False,
}

# In-memory cache: {(symbol, YYYY-MM-DD): result_dict}. Lost on server reload.
_CACHE: dict[tuple[str, str], dict] = {}
_CACHE_LOCK = threading.Lock()


# ─── yfinance fetchers ────────────────────────────────────────────────────────

def fetch_fundamentals(symbol: str) -> dict:
    info = yf.Ticker(symbol).info or {}
    if not info.get("symbol") and not info.get("shortName"):
        raise LookupError(f"Unknown ticker {symbol!r}")
    return {k: info[k] for k in _FUNDAMENTAL_KEYS if k in info and info[k] is not None}


def fetch_news(symbol: str, limit: int = 10) -> list[dict]:
    """Defensive accessor — yfinance changed the news shape in 2024 (fields
    moved under `content`). Handle both layouts."""
    raw = yf.Ticker(symbol).news or []
    out = []
    for item in raw[:limit]:
        content = item.get("content") or item
        title = content.get("title") or item.get("title")
        publisher = (
            (content.get("provider") or {}).get("displayName")
            or item.get("publisher")
        )
        link = (
            (content.get("canonicalUrl") or {}).get("url")
            or content.get("clickThroughUrl", {}).get("url")
            or item.get("link")
        )
        published = (
            content.get("pubDate")
            or content.get("displayTime")
            or item.get("providerPublishTime")
        )
        if title:
            out.append({
                "title": title,
                "publisher": publisher,
                "link": link,
                "published": str(published) if published else None,
            })
    return out


# ─── Claude orchestration ─────────────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    """Try fenced ```json first, then any balanced {...} block."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        return json.loads(brace.group(0))
    raise ValueError("No JSON object found in model output")


def _build_user_message(symbol: str, fundamentals: dict, news: list[dict]) -> str:
    parts = [f"SYMBOL: {symbol}", "", "FUNDAMENTALS:", json.dumps(fundamentals, indent=2)]
    if news:
        parts += ["", "RECENT HEADLINES:"]
        for n in news:
            line = f"- {n.get('published', '?')} — {n.get('publisher') or '?'} — {n['title']}"
            parts.append(line)
    return "\n".join(parts)


def _call_claude(symbol: str, fundamentals: dict, news: list[dict]) -> tuple[dict, list[dict]]:
    client = anthropic.Anthropic()
    user_text = _build_user_message(symbol, fundamentals, news)

    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_text}],
        tools=[{"type": "web_search_20260209", "name": "web_search"}],
        output_config={"format": {"type": "json_schema", "schema": _JSON_SCHEMA}},
    )

    # Find the last text block — that's the JSON. Earlier blocks may be
    # server_tool_use / web_search_tool_result; we don't need those.
    text = next(
        (b.text for b in reversed(response.content) if getattr(b, "type", "") == "text"),
        "",
    )
    if not text:
        raise RuntimeError("Claude returned no text content")
    thesis = _extract_json(text)
    sources = thesis.pop("sources", [])
    return thesis, sources


def research(symbol: str) -> dict:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    if len(symbol) > 16 or not _SYMBOL_RE.match(symbol):
        raise ValueError(f"invalid symbol {symbol!r}")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = (symbol, today)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached:
        return {**cached, "cached": True}

    fundamentals = fetch_fundamentals(symbol)
    news = fetch_news(symbol)

    thesis, sources = _call_claude(symbol, fundamentals, news)

    result = {
        "symbol":       symbol,
        "fundamentals": fundamentals,
        "thesis":       thesis,
        "sources":      sources,
        "fetched_at":   datetime.now(timezone.utc).isoformat(),
        "cached":       False,
    }
    with _CACHE_LOCK:
        _CACHE[key] = {**result, "cached": False}
    return result
