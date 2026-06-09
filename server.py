"""
Dashboard Server
================
Serves the dashboard HTML and exposes the bot state, market data, strategy,
and copy-trading APIs.

Install:  pip install fastapi uvicorn
Run:      python server.py
Open:     http://localhost:8000
"""

import json
import threading
import time
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn
import yfinance as yf

import backtest as bt_engine
import strategies as strategies_pkg
import congress_data
import superinvestor_data
import portfolio
import momentum_live

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

DATA_FILE = Path("data.json")


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
    # Backtests default to FULL deployment so the result answers "did this beat
    # holding the market?" (live risk sizing is a separate config). The benchmark
    # mirrors whatever fraction is used, so the comparison stays fair either way.
    position_size_pct: float = 100.0
    slippage_bps: float = 5.0          # per-fill cost (Alpaca equities are commission-free)
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
            slippage_bps=req.slippage_bps,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(result)


@app.get("/api/strategies")
def list_strategies():
    return JSONResponse({"strategies": strategies_pkg.list_all()})


# ─────────────────────────────────────────────
#  MOMENTUM PORTFOLIO (multi-asset, validated)
# ─────────────────────────────────────────────

class MomentumRequest(BaseModel):
    top_n: int = 2
    lookback: int = 12          # months
    cost_bps: float = 5.0


@app.post("/api/momentum/backtest")
def momentum_backtest(req: MomentumRequest):
    try:
        return JSONResponse(portfolio.backtest_api(
            top_n=req.top_n, lookback=req.lookback, cost_bps=req.cost_bps))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/momentum/current")
def momentum_current(top_n: int = 2, lookback: int = 12):
    """What the strategy says to hold right now (the live signal)."""
    try:
        return JSONResponse(portfolio.current_target(top_n=top_n, lookback=lookback))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/momentum/rebalance/preview")
def momentum_rebalance_preview(top_n: int = 2, lookback: int = 12):
    """Compute the exact paper orders to reach target — places nothing."""
    try:
        return JSONResponse(momentum_live.preview(top_n=top_n, lookback=lookback))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/momentum/rebalance/execute")
def momentum_rebalance_execute(req: MomentumRequest):
    """Submit the rebalance orders to the Alpaca PAPER account (explicit confirm)."""
    try:
        return JSONResponse(momentum_live.execute(top_n=req.top_n, lookback=req.lookback))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


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
#  CONGRESS COPY TRADING
# ─────────────────────────────────────────────

class SimulateRequest(BaseModel):
    member_id: str
    capital: float = 10_000.0
    sizing: str = "equal"  # "equal" or "amount" (mirror member's disclosed size)


@app.get("/api/congress/members")
def congress_members():
    try:
        return JSONResponse({
            "members": congress_data.member_list(),
            "mock": congress_data.is_mock(),
        })
    except (congress_data.MissingAPIKey, congress_data.APIUnavailable) as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/congress/trades")
def congress_trades(days: int = 180):
    try:
        return JSONResponse({"trades": congress_data.fetch_trades(days=days)})
    except (congress_data.MissingAPIKey, congress_data.APIUnavailable) as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/congress/member/{member_id}")
def congress_member(member_id: str):
    try:
        trades = congress_data.attach_trade_prices(congress_data.member_trades(member_id))
        performance = congress_data.member_performance(member_id)
        return JSONResponse({"trades": trades, "performance": performance})
    except (congress_data.MissingAPIKey, congress_data.APIUnavailable) as e:
        raise HTTPException(status_code=503, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/congress/simulate")
def congress_simulate(req: SimulateRequest):
    try:
        return JSONResponse(congress_data.simulate_follow(req.member_id, req.capital, sizing=req.sizing))
    except (congress_data.MissingAPIKey, congress_data.APIUnavailable) as e:
        raise HTTPException(status_code=503, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ─────────────────────────────────────────────
#  SUPERINVESTOR (SEC 13F via Dataroma) COPY TRADING
# ─────────────────────────────────────────────
#  Second COPY source. Same shapes/contract as the Congress routes above (and the
#  same SimulateRequest model), so static/js/copy.js can drive both with only a
#  different apiBase. superinvestor_data reuses the congress_data engine.

@app.get("/api/superinvestors/members")
def superinvestors_members():
    try:
        return JSONResponse({
            "members": superinvestor_data.member_list(),
            "mock": superinvestor_data.is_mock(),
        })
    except superinvestor_data.APIUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/superinvestors/member/{member_id}")
def superinvestors_member(member_id: str):
    try:
        trades = superinvestor_data.attach_trade_prices(superinvestor_data.member_trades(member_id))
        performance = superinvestor_data.member_performance(member_id)
        return JSONResponse({"trades": trades, "performance": performance})
    except superinvestor_data.APIUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/superinvestors/simulate")
def superinvestors_simulate(req: SimulateRequest):
    try:
        return JSONResponse(superinvestor_data.simulate_follow(req.member_id, req.capital, sizing=req.sizing))
    except superinvestor_data.APIUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/live")
def live_account():
    """Live Alpaca paper account state for the LIVE tab."""
    try:
        return JSONResponse(momentum_live.account_snapshot())
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/live/history")
def live_history():
    """Account equity curve over time (for the LIVE chart)."""
    try:
        return JSONResponse(momentum_live.portfolio_history())
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/live/orders")
def live_orders(limit: int = 50):
    """The account's recent filled trades (for the LIVE trades table)."""
    try:
        return JSONResponse(momentum_live.recent_orders(limit=limit))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/", response_class=HTMLResponse)
def dashboard():
    html = Path("dashboard.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
