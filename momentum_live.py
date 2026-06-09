"""
Momentum live executor (Alpaca PAPER)
=====================================
Rebalances an Alpaca **paper** account to the Top-N momentum target computed by
``portfolio.current_target()``. Deliberately manual + preview-first:

    preview()  → compute target + read account → return the EXACT orders, place nothing
    execute()  → actually submit those orders (paper only)

Safety rails: hard ``paper=True`` client; a no-trade band so we don't churn on tiny
drift; full exits use ``close_position``; and with no keys configured ``preview`` still
works against a hypothetical cash account so you can see what it *would* do before
connecting anything.

Keys are read from the environment (a ``.env`` next to this file) first, then
``config.py`` as a fallback. Set in ``.env``:
    ALPACA_API_KEY=...
    ALPACA_SECRET_KEY=...
"""

import os
from datetime import datetime, timezone

import portfolio

MIN_TRADE_USD = 50.0   # ignore dust trades
DRIFT_BAND = 0.02      # only trade a name if it's >2% of the portfolio off target


# ── .env loader (stdlib, same pattern as congress_data) ─────────────────────────

def _load_env_file() -> None:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:
        pass


_load_env_file()


def _alpaca_keys():
    try:
        from config import ALPACA_API_KEY as ck, ALPACA_SECRET_KEY as cs
    except Exception:
        ck, cs = "", ""
    key = (os.getenv("ALPACA_API_KEY") or ck or "").strip()
    sec = (os.getenv("ALPACA_SECRET_KEY") or cs or "").strip()
    if key in ("", "YOUR_API_KEY_HERE"):
        key = ""
    if sec in ("", "YOUR_SECRET_KEY_HERE"):
        sec = ""
    return key, sec


def _client():
    """An Alpaca PAPER trading client, or None if keys aren't configured."""
    key, sec = _alpaca_keys()
    if not key or not sec:
        return None
    from alpaca.trading.client import TradingClient
    return TradingClient(key, sec, paper=True)  # paper-only, always


def _account_state(client):
    acct = client.get_account()
    value = float(acct.portfolio_value)
    positions = {p.symbol: float(p.market_value) for p in client.get_all_positions()}
    return value, positions


def portfolio_history(period: str = "1D", timeframe: str = "5Min") -> dict:
    """Account equity over time (Alpaca portfolio history) for the LIVE chart.
    Defaults to today's intraday (5-min) curve — the most informative view for a
    young account; pass period='1M', timeframe='1D' for a longer daily curve.
    Drops any leading 0.0 points (from before the account was funded)."""
    from datetime import datetime as _dt
    client = _client()
    if client is None:
        return {"connected": False, "curve": []}
    try:
        from alpaca.trading.requests import GetPortfolioHistoryRequest
        ph = client.get_portfolio_history(
            history_filter=GetPortfolioHistoryRequest(period=period, timeframe=timeframe))
    except Exception:
        return {"connected": True, "curve": []}
    intraday = ("Min" in timeframe) or ("H" in timeframe)
    curve = []
    for t, v in zip(ph.timestamp or [], ph.equity or []):
        if not v:  # skip pre-funding zeros
            continue
        dt = _dt.fromtimestamp(int(t))  # local time (server runs on the user's machine)
        curve.append({"date": dt.strftime("%H:%M" if intraday else "%m/%d"),
                      "value": round(float(v), 2)})
    return {"connected": True, "curve": curve,
            "base_value": float(ph.base_value or 0), "intraday": intraday}


def recent_orders(limit: int = 50) -> dict:
    """The account's actual filled trades (Alpaca closed orders that executed)."""
    client = _client()
    if client is None:
        return {"connected": False, "orders": []}
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        raw = client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=limit))
    except Exception:
        return {"connected": True, "orders": []}

    def _enum(v):
        return v.value if hasattr(v, "value") else str(v).split(".")[-1].lower()

    out = []
    for o in raw:
        if not o.filled_at:          # only real (executed) trades
            continue
        fq = float(o.filled_qty) if o.filled_qty else 0.0
        fp = float(o.filled_avg_price) if o.filled_avg_price else 0.0
        val = float(o.notional) if getattr(o, "notional", None) else round(fq * fp, 2)
        out.append({
            "symbol": o.symbol,
            "side": _enum(o.side),                 # "buy" / "sell"
            "qty": round(fq, 4),
            "price": round(fp, 2),
            "value": round(val, 2),
            "filled_at": str(o.filled_at),
        })
    out.sort(key=lambda x: x["filled_at"], reverse=True)
    return {"connected": True, "orders": out}


def account_snapshot() -> dict:
    """Live Alpaca PAPER account snapshot for the dashboard LIVE tab: portfolio
    value, today's P&L, cash, and every open position with its unrealized P&L."""
    client = _client()
    if client is None:
        return {"connected": False,
                "note": ("No Alpaca paper keys configured. Add ALPACA_API_KEY and "
                         "ALPACA_SECRET_KEY to a .env file next to server.py, then reload.")}
    acct = client.get_account()
    equity = float(acct.portfolio_value)
    last_equity = float(getattr(acct, "last_equity", 0) or 0) or equity  # prior close
    positions, open_pl = [], 0.0
    for p in client.get_all_positions():
        mv = float(p.market_value)
        upl = float(p.unrealized_pl)
        open_pl += upl
        positions.append({
            "symbol": p.symbol,
            "qty": round(float(p.qty), 4),
            "avg_entry": round(float(p.avg_entry_price), 2),
            "price": round(float(p.current_price), 2),
            "market_value": round(mv, 2),
            "unrealized_pl": round(upl, 2),
            "unrealized_plpc": round(float(p.unrealized_plpc) * 100, 2),
            "change_today_pct": round(float(getattr(p, "change_today", 0) or 0) * 100, 2),
            "weight_pct": round(mv / equity * 100, 1) if equity else 0.0,
        })
    positions.sort(key=lambda x: x["market_value"], reverse=True)
    return {
        "connected": True,
        "portfolio_value": round(equity, 2),
        "cash": round(float(acct.cash), 2),
        "buying_power": round(float(acct.buying_power), 2),
        "pl_today": round(equity - last_equity, 2),
        "pl_today_pct": round((equity - last_equity) / last_equity * 100, 2) if last_equity else 0.0,
        "open_pl": round(open_pl, 2),
        "num_positions": len(positions),
        "positions": positions,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Pure planner (no network — unit-testable) ───────────────────────────────────

def plan_rebalance(target_pct: dict, positions: dict, portfolio_value: float,
                   min_trade=MIN_TRADE_USD, band=DRIFT_BAND) -> list:
    """Target weights (%) + current positions ($) + total value → list of orders.
    Sells first (to free cash), then buys. Names within the drift band are left alone."""
    orders = []
    threshold = max(min_trade, band * portfolio_value)
    for sym in set(target_pct) | set(positions):
        target_val = portfolio_value * target_pct.get(sym, 0.0) / 100.0
        cur_val = positions.get(sym, 0.0)
        diff = target_val - cur_val
        full_exit = target_pct.get(sym, 0.0) == 0 and cur_val > 0
        if not full_exit and abs(diff) < threshold:
            continue
        if abs(diff) < min_trade:
            continue
        orders.append({
            "symbol": sym,
            "side": "buy" if diff > 0 else "sell",
            "notional": round(abs(diff), 2),
            "close": full_exit,
            "current": round(cur_val, 2),
            "target": round(target_val, 2),
        })
    orders.sort(key=lambda o: 0 if o["side"] == "sell" else 1)
    return orders


# ── Preview / execute ───────────────────────────────────────────────────────────

def preview(top_n=2, lookback=12, capital_if_offline=10_000.0) -> dict:
    """Compute the target and the exact orders to reach it — WITHOUT placing anything."""
    tgt = portfolio.current_target(top_n=top_n, lookback=lookback)
    client = _client()
    connected = client is not None
    if connected:
        value, positions = _account_state(client)
    else:
        value, positions = float(capital_if_offline), {}
    orders = plan_rebalance(tgt["weights"], positions, value)
    return {
        "connected": connected,
        "as_of": tgt["as_of"],
        "target": tgt["weights"],
        "portfolio_value": round(value, 2),
        "positions": {k: round(v, 2) for k, v in positions.items()},
        "orders": orders,
        "note": ("Connected to your Alpaca paper account." if connected else
                 f"No Alpaca keys yet — preview assumes a fresh ${capital_if_offline:,.0f} "
                 "cash account. Add ALPACA_API_KEY / ALPACA_SECRET_KEY to .env to go live."),
    }


def execute(top_n=2, lookback=12) -> dict:
    """Submit the rebalance orders to the Alpaca PAPER account."""
    client = _client()
    if client is None:
        raise RuntimeError("No Alpaca paper keys configured. Add ALPACA_API_KEY / "
                           "ALPACA_SECRET_KEY to a .env file, then retry.")
    value, positions = _account_state(client)
    tgt = portfolio.current_target(top_n=top_n, lookback=lookback)
    orders = plan_rebalance(tgt["weights"], positions, value)

    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    placed = []
    for o in orders:
        try:
            if o["close"]:
                res = client.close_position(o["symbol"])
            else:
                req = MarketOrderRequest(
                    symbol=o["symbol"], notional=o["notional"],
                    side=OrderSide.BUY if o["side"] == "buy" else OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                )
                res = client.submit_order(req)
            placed.append({**o, "status": "submitted", "id": str(getattr(res, "id", ""))})
        except Exception as e:
            placed.append({**o, "status": "error", "error": str(e)})
    return {
        "as_of": tgt["as_of"], "target": tgt["weights"], "orders": placed,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(preview(), indent=2))
