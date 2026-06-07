"""
Ticker research (free, yfinance-only)
======================================
Pulls fundamentals, recent news, and 1-year price history from yfinance.
No AI / API key required. Results cached per (symbol, hour) in-memory.
"""

import re
import threading
from datetime import datetime, timezone

import yfinance as yf

_FUNDAMENTAL_KEYS = (
    "marketCap", "trailingPE", "forwardPE", "profitMargins",
    "returnOnEquity", "debtToEquity", "revenueGrowth", "dividendYield",
    "beta", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
    "sector", "industry", "longBusinessSummary",
)

_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-]+$")

_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()


def fetch_fundamentals(symbol: str) -> dict:
    info = yf.Ticker(symbol).info or {}
    if not info.get("symbol") and not info.get("shortName"):
        raise LookupError(f"Unknown ticker {symbol!r}")
    return {k: info[k] for k in _FUNDAMENTAL_KEYS if k in info and info[k] is not None}


def fetch_news(symbol: str, limit: int = 12) -> list:
    """Handles both old and new yfinance news layouts."""
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


def fetch_price_history(symbol: str) -> list:
    """Weekly closes for the past year — used for the mini sparkline."""
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period="1y", interval="1wk", auto_adjust=True)
    if hist.empty:
        return []
    return [
        {"date": str(idx.date()), "close": round(float(row["Close"]), 2)}
        for idx, row in hist.iterrows()
    ]


def research(symbol: str) -> dict:
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    if len(symbol) > 16 or not _SYMBOL_RE.match(symbol):
        raise ValueError(f"invalid symbol {symbol!r}")

    # Cache key by symbol + hour so data refreshes roughly every hour
    hour = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")
    key = (symbol, hour)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached:
        return {**cached, "cached": True}

    fundamentals = fetch_fundamentals(symbol)
    news = fetch_news(symbol)
    price_history = fetch_price_history(symbol)

    result = {
        "symbol": symbol,
        "fundamentals": fundamentals,
        "news": news,
        "price_history": price_history,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "cached": False,
    }
    with _CACHE_LOCK:
        _CACHE[key] = {**result, "cached": False}
    return result
