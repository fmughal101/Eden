"""
Dashboard Server
================
Serves the dashboard HTML, exposes bot state + journal API, and receives
TradingView webhooks.

Install:  pip install fastapi uvicorn
Run:      python server.py
Open:     http://localhost:8000

Webhook secret: set env var TV_WEBHOOK_SECRET before running, e.g.
  PowerShell:  $env:TV_WEBHOOK_SECRET = "your-long-random-string"
  Bash:        export TV_WEBHOOK_SECRET="your-long-random-string"
"""

import json
import os
import threading
import time
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn
import yfinance as yf

import backtest as bt_engine
import strategies as strategies_pkg
import journal
import research as research_module

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

DATA_FILE = Path("data.json")
WEBHOOK_SECRET = os.getenv("TV_WEBHOOK_SECRET", "change-me-in-production")

journal.init_db()


def read_bot_data() -> dict:
    if not DATA_FILE.exists():
        return {
            "status": "offline",
            "symbol": "SPY",
            "portfolio_value": 1259677.33,
            "initial_capital": 1259677.33,
            "current_price": None,
            "shares_held": 0,
            "sma_short": None,
            "sma_long": None,
            "signal": 0,
            "short_window": 20,
            "long_window": 50,
            "stop_loss_pct": 3.0,
            "position_size_pct": 10.0,
            "trades": [],
            "price_history": [],
            "last_updated": None,
        }
    with open(DATA_FILE) as f:
        return json.load(f)


@app.get("/api/state")
def get_state():
    return JSONResponse(read_bot_data())


class BacktestRequest(BaseModel):
    strategy: str = "sma_crossover"
    symbol: str = "SPY"
    params: dict = {}
    stop_loss_pct: float = 3.0
    position_size_pct: float = 10.0
    initial_capital: float = 10_000.0
    period: str = "2y"


@app.post("/api/backtest")
def backtest_endpoint(req: BacktestRequest):
    try:
        result = bt_engine.run(
            strategy_key=req.strategy,
            params=req.params,
            symbol=req.symbol,
            period=req.period,
            stop_loss_pct=req.stop_loss_pct / 100.0,
            position_size_pct=req.position_size_pct / 100.0,
            initial_capital=req.initial_capital,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(result)


@app.get("/api/strategies")
def list_strategies():
    return JSONResponse({"strategies": strategies_pkg.list_all()})


# ─────────────────────────────────────────────
#  TICKER TAPE QUOTES
# ─────────────────────────────────────────────
# Symbols displayed in the ticker tape. Yahoo uses different tickers for crypto
# and class-B shares — TICKER_OVERRIDES maps the UI label to the yfinance symbol.
TICKER_DISPLAY = [
    "SPY",  "QQQ",  "AAPL", "MSFT", "NVDA", "TSLA",
    "AMZN", "GOOG", "META", "AMD",  "BRK.B", "JPM",
    "XLE",  "XLF",  "GLD",  "TLT",  "BTC",   "ETH",
]
TICKER_OVERRIDES = {"BRK.B": "BRK-B", "BTC": "BTC-USD", "ETH": "ETH-USD"}

_QUOTES_TTL_SECONDS = 60
_quotes_cache: dict = {"data": None, "expires": 0.0}
_quotes_lock = threading.Lock()


def _fetch_quotes() -> list[dict]:
    fetch_syms = [TICKER_OVERRIDES.get(s, s) for s in TICKER_DISPLAY]
    df = yf.download(
        fetch_syms,
        period="5d",
        progress=False,
        auto_adjust=True,
        group_by="ticker",
        threads=True,
    )
    quotes = []
    for display in TICKER_DISPLAY:
        fetch_sym = TICKER_OVERRIDES.get(display, display)
        try:
            closes = df[fetch_sym]["Close"].dropna()
            if len(closes) < 2:
                continue
            last = float(closes.iloc[-1])
            prev = float(closes.iloc[-2])
            pct = (last - prev) / prev * 100.0
            quotes.append({"symbol": display, "price": last, "pct": pct})
        except Exception:
            continue
    return quotes


class ResearchRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16)


@app.post("/api/research")
def research_endpoint(req: ResearchRequest):
    if not research_module.ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")
    try:
        return JSONResponse(research_module.research(req.symbol))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Research failed: {e}")


@app.get("/api/quotes")
def get_quotes():
    now = time.time()
    with _quotes_lock:
        if _quotes_cache["data"] is not None and now < _quotes_cache["expires"]:
            return JSONResponse({"quotes": _quotes_cache["data"], "cached": True})
    try:
        quotes = _fetch_quotes()
    except Exception as e:
        # On failure, serve stale cache if available, otherwise propagate
        with _quotes_lock:
            stale = _quotes_cache["data"]
        if stale:
            return JSONResponse({"quotes": stale, "cached": True, "stale": True})
        raise HTTPException(status_code=503, detail=f"Quote fetch failed: {e}")
    with _quotes_lock:
        _quotes_cache["data"] = quotes
        _quotes_cache["expires"] = now + _QUOTES_TTL_SECONDS
    return JSONResponse({"quotes": quotes, "cached": False})


# ─────────────────────────────────────────────
#  TRADINGVIEW WEBHOOK
# ─────────────────────────────────────────────

ALLOWED_ACTIONS = {"BUY", "SELL", "CLOSE"}


class TradingViewSignal(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16)
    action: str
    price: float | None = None
    stop: float | None = None
    strategy: str | None = None


@app.post("/webhook/tradingview")
async def tradingview_webhook(
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
):
    # Read raw body first so we can log even malformed payloads
    raw_bytes = await request.body()
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except Exception:
        journal.log_signal(
            source="tradingview",
            payload={"raw": raw_bytes.decode("utf-8", errors="replace")},
            status="rejected",
            notes="Invalid JSON",
        )
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Authenticate — secret accepted via header OR payload.secret field
    provided = x_webhook_secret or payload.get("secret")
    if provided != WEBHOOK_SECRET:
        journal.log_signal(
            source="tradingview",
            payload=payload,
            status="rejected",
            notes="Bad or missing secret",
        )
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Strip the secret from the payload we log (don't store it in plaintext)
    safe_payload = {k: v for k, v in payload.items() if k != "secret"}

    try:
        signal = TradingViewSignal(**safe_payload)
    except Exception as e:
        journal.log_signal(
            source="tradingview",
            payload=safe_payload,
            status="rejected",
            notes=f"Schema error: {e}",
        )
        raise HTTPException(status_code=400, detail=f"Invalid signal: {e}")

    action = signal.action.upper().strip()
    if action not in ALLOWED_ACTIONS:
        journal.log_signal(
            source="tradingview",
            payload=safe_payload,
            status="rejected",
            notes=f"Unknown action '{signal.action}' (expected BUY/SELL/CLOSE)",
            symbol=signal.symbol,
            action=signal.action,
        )
        raise HTTPException(
            status_code=400,
            detail=f"action must be one of {sorted(ALLOWED_ACTIONS)}",
        )

    signal_id = journal.log_signal(
        source="tradingview",
        payload=safe_payload,
        status="received",
        symbol=signal.symbol.upper(),
        action=action,
        price=signal.price,
        stop=signal.stop,
        strategy=signal.strategy,
    )

    # Step 2 (next iteration) will place the Alpaca paper order here and
    # call journal.update_signal(signal_id, status="executed", order_id=..., ...)

    return {"ok": True, "signal_id": signal_id, "status": "received"}


@app.get("/api/signals")
def list_signals(limit: int = 100):
    return JSONResponse({
        "signals": journal.get_recent_signals(limit=limit),
        "stats":   journal.get_signal_stats(),
    })


@app.get("/", response_class=HTMLResponse)
def dashboard():
    html = Path("dashboard.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


if __name__ == "__main__":
    if WEBHOOK_SECRET == "change-me-in-production":
        print("\n⚠️  WARNING: TV_WEBHOOK_SECRET env var is not set. Using default.")
        print("   Set it before going live:  $env:TV_WEBHOOK_SECRET = \"...\"\n")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
