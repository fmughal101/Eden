"""
Portfolio backtest engine (multi-asset)
=======================================
Upgrades the single-asset backtester to a real PORTFOLIO engine: hold a basket,
rebalance monthly, pay turnover costs, mark to market daily. Scored on the same
honest ruler as everything else (``metrics``), against buy-and-hold AND 60/40.

It exists to answer one question honestly: does **dual momentum** (rotate into
what's trending; go defensive to bonds when nothing is) actually earn its keep
versus just holding the market — risk-adjusted, after costs?

Strategies are "weight generators": given aligned daily closes, emit a table of
target weights at each monthly rebalance (indexed by the actual last trading day
of each month). ``simulate`` turns weights → a daily equity curve with drift +
turnover costs. Reusable for any allocation rule.
"""

import warnings

import numpy as np
import pandas as pd
import yfinance as yf

import metrics

warnings.simplefilter("ignore")  # quiet pandas resample/yfinance chatter

TRADING_DAYS = 252


# ── Data ────────────────────────────────────────────────────────────────────────

def fetch_closes(symbols: list, period: str = "max") -> pd.DataFrame:
    """Aligned daily adjusted closes for the basket (common date range only)."""
    df = yf.download(symbols, period=period, auto_adjust=True, progress=False)
    closes = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df.rename(columns={"Close": symbols[0]})[symbols[0]].to_frame()
    closes = closes[symbols] if set(symbols).issubset(closes.columns) else closes
    return closes.dropna(how="any")  # intersection → every asset has history


def _monthly(closes: pd.DataFrame):
    """Month-end closes + the actual last trading day of each month."""
    m_close = closes.resample("ME").last()
    m_date = closes.index.to_series().resample("ME").last()
    return m_close, m_date


# ── Weight generators (strategies) ──────────────────────────────────────────────

def gem_weights(closes, risk=("SPY", "EFA"), safe="AGG", lookback=12) -> pd.DataFrame:
    """Antonacci-style Global Equities Momentum: each month hold the best risk
    asset by trailing return — but only if it beat the safe asset (absolute
    momentum); otherwise hold the safe asset (bonds)."""
    m_close, m_date = _monthly(closes)
    rows = {}
    for i in range(lookback, len(m_close)):
        trailing = m_close.iloc[i] / m_close.iloc[i - lookback] - 1
        best = max(risk, key=lambda s: trailing[s])
        w = {s: 0.0 for s in closes.columns}
        w[best if trailing[best] > trailing[safe] else safe] = 1.0
        rows[m_date.iloc[i]] = w
    return pd.DataFrame(rows).T.reindex(columns=closes.columns).fillna(0.0)


def topn_weights(closes, assets, n=2, safe="AGG", lookback=12) -> pd.DataFrame:
    """Rank a basket by trailing return, hold the top N (equal weight). Any slot
    whose asset has negative absolute momentum goes to the safe asset instead."""
    m_close, m_date = _monthly(closes)
    rows = {}
    for i in range(lookback, len(m_close)):
        trailing = m_close.iloc[i] / m_close.iloc[i - lookback] - 1
        ranked = sorted(assets, key=lambda s: trailing[s], reverse=True)
        chosen = [s for s in ranked[:n] if trailing[s] > 0]
        w = {s: 0.0 for s in closes.columns}
        if chosen:
            for s in chosen:
                w[s] = 1.0 / n
            if len(chosen) < n:
                w[safe] = w.get(safe, 0.0) + (n - len(chosen)) / n
        else:
            w[safe] = 1.0
        rows[m_date.iloc[i]] = w
    return pd.DataFrame(rows).T.reindex(columns=closes.columns).fillna(0.0)


def const_weights(closes, alloc: dict, start, lookback=12) -> pd.DataFrame:
    """Fixed allocation, rebalanced monthly (e.g. 60/40). Starts at `start` so it
    lines up with the momentum strategies' first live month."""
    m_close, m_date = _monthly(closes)
    rows = {}
    for i in range(lookback, len(m_close)):
        td = m_date.iloc[i]
        if td >= start:
            rows[td] = {s: alloc.get(s, 0.0) for s in closes.columns}
    return pd.DataFrame(rows).T.reindex(columns=closes.columns).fillna(0.0)


def buyhold_weights(closes, sym, start) -> pd.DataFrame:
    """100% one asset, bought once at `start` and held (no rebalancing)."""
    return pd.DataFrame({sym: 1.0}, index=[start]).reindex(columns=closes.columns).fillna(0.0)


# ── Simulation ──────────────────────────────────────────────────────────────────

def simulate(closes, weights, initial_capital=10000.0, cost_bps=5.0) -> list:
    """Daily equity curve from a monthly target-weight table. Holdings drift with
    prices between rebalances; at each rebalance we trade back to target and pay
    ``cost_bps`` on the turnover."""
    daily_ret = closes.pct_change().fillna(0.0)
    rebal = {d: weights.loc[d] for d in weights.index}
    start = weights.index[0]
    cost = cost_bps / 10_000.0
    holdings = {s: 0.0 for s in closes.columns}
    equity = float(initial_capital)
    deployed = False
    curve = []
    for date in closes.index:
        if date < start:
            continue
        if deployed:
            for s in holdings:
                holdings[s] *= (1 + daily_ret.at[date, s])
            equity = sum(holdings.values())
        if date in rebal:
            tgt = rebal[date]
            new = {s: equity * float(tgt[s]) for s in closes.columns}
            turnover = sum(abs(new[s] - holdings[s]) for s in closes.columns) / equity if equity > 0 else 0.0
            equity -= equity * turnover * cost
            holdings = {s: equity * float(tgt[s]) for s in closes.columns}
            deployed = True
        curve.append({"date": str(date.date()), "value": round(equity, 2)})
    return curve


# Default universe: risk basket + safe (defensive) asset.
RISK_BASKET = ["SPY", "QQQ", "EFA", "GLD"]
SAFE_ASSET = "AGG"
UNIVERSE = RISK_BASKET + [SAFE_ASSET]


def current_target(top_n=2, lookback=12, period="3y") -> dict:
    """What the Top-N momentum strategy says to HOLD right now — the bridge from
    backtest to live execution. Returns {symbol: weight_pct}. (period='3y' is
    plenty of history for a 12-month lookback and keeps it fast.)"""
    closes = fetch_closes(UNIVERSE, period)
    w = topn_weights(closes, RISK_BASKET, n=top_n, safe=SAFE_ASSET, lookback=lookback)
    last = w.iloc[-1]
    return {
        "as_of": str(w.index[-1].date()),
        "weights": {s: round(float(last[s]) * 100, 1) for s in closes.columns if last[s] > 0},
    }


def _downsample(curve, max_points=1100):
    if len(curve) <= max_points:
        return curve
    step = len(curve) // max_points + 1
    ds = curve[::step]
    if ds[-1]["date"] != curve[-1]["date"]:
        ds.append(curve[-1])
    return ds


def _compute(top_n=2, lookback=12, cost_bps=5.0, period="max") -> dict:
    """Backtest Top-N momentum + GEM vs 60/40 + buy-and-hold SPY, all honest."""
    closes = fetch_closes(UNIVERSE, period)
    top = topn_weights(closes, RISK_BASKET, n=top_n, safe=SAFE_ASSET, lookback=lookback)
    gem = gem_weights(closes, risk=("SPY", "EFA"), safe=SAFE_ASSET, lookback=lookback)
    start = top.index[0]
    bh = buyhold_weights(closes, "SPY", start)
    p6040 = const_weights(closes, {"SPY": 0.6, SAFE_ASSET: 0.4}, start, lookback)

    specs = [(f"Top-{top_n} Momentum", top), ("Dual Momentum (GEM)", gem),
             ("60/40 SPY-AGG", p6040), ("Buy & Hold SPY", bh)]
    out = []
    for name, w in specs:
        curve = simulate(closes, w, 10_000.0, cost_bps)
        out.append({"name": name, "metrics": metrics.compute(curve, periods_per_year=TRADING_DAYS),
                    "equity_curve": curve})

    last = top.iloc[-1]
    return {
        "strategies": out,
        "current_target": {s: round(float(last[s]) * 100, 1) for s in closes.columns if last[s] > 0},
        "current_date": str(top.index[-1].date()),
        "start": str(start.date()), "end": str(closes.index[-1].date()),
        "basket": RISK_BASKET, "safe": SAFE_ASSET,
        "params": {"top_n": top_n, "lookback": lookback, "cost_bps": cost_bps},
    }


def backtest_api(top_n=2, lookback=12, cost_bps=5.0, period="max") -> dict:
    """Dashboard-shaped result: full-curve metrics, downsampled curves for charting."""
    res = _compute(top_n, lookback, cost_bps, period)
    for s in res["strategies"]:
        s["equity_curve"] = _downsample(s["equity_curve"])
    return res


if __name__ == "__main__":
    res = _compute()
    print(f"Period: {res['start']} → {res['end']}  basket={res['basket']} safe={res['safe']}")
    print(f"Current target ({res['current_date']}): {res['current_target']}\n")
    print(f"{'STRATEGY':22} {'TOTAL':>9} {'CAGR':>7} {'MAXDD':>7} {'SHARPE':>7} {'SORTINO':>8} {'VOL':>7}")
    for s in res["strategies"]:
        m = s["metrics"]
        print(f"{s['name']:22} {m['total_return_pct']:>8.0f}% {m['cagr_pct']:>6.1f}% "
              f"{m['max_drawdown_pct']:>6.1f}% {m['sharpe']:>7.2f} {m['sortino']:>8.2f} {m['volatility_pct']:>6.1f}%")
