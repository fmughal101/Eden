"""
Backtest engine (honest)
========================
Runs a strategy plugin over historical data and reports what would *actually*
have happened — after costs, with no look-ahead — and, crucially, next to a
buy-and-hold benchmark so you can see whether the strategy added anything.

Honesty guarantees (vs a naive backtest):
  • Costs: every fill pays slippage (bps); optional commission per trade.
  • No look-ahead: a signal computed from bar i's close is acted on at bar
    i+1's OPEN, not the same bar. (Stop-losses are intrabar, on the bar's low.)
  • Mark-to-market equity curve every bar → real drawdown / Sharpe / etc.
  • Benchmark: buy & hold the same symbol at the SAME deployment, same costs, so
    the comparison isolates timing, not how much capital you happened to deploy.
Metrics come from the shared ``metrics`` module (also used by the copy-trading
simulators), so every signal source is judged on one honest ruler.
"""

import pandas as pd

from data import get_historical_data
import strategies
import metrics

TRADING_DAYS = 252  # daily bars


def _col(row, name, default):
    """Safe OHLC accessor (some symbols/rows may miss a column)."""
    if name in row and not pd.isna(row[name]):
        return float(row[name])
    return default


def simulate_trades(df: pd.DataFrame, stop_loss_pct: float, position_size_pct: float,
                    initial_capital: float, slippage_bps: float = 5.0,
                    commission: float = 0.0) -> dict:
    """Event-driven sim with costs, next-bar fills and a per-bar equity curve.

    Signals are sparse (+1 buy / -1 sell on crossover bars, 0 otherwise); we act
    on the PREVIOUS bar's signal at the current bar's open. Position sizing
    deploys ``position_size_pct`` of available cash per entry (fractional shares
    allowed, matching Alpaca). No margin — buys are funded from cash only.
    """
    slip = slippage_bps / 10_000.0
    cash = float(initial_capital)
    shares = 0.0
    entry_price = None
    trades: list = []
    equity_curve: list = []
    bars_in_market = 0

    prev_signal = 0
    rows = list(df.iterrows())
    for idx, row in rows:
        close = _col(row, "Close", 0.0)
        open_ = _col(row, "Open", close)        # fill price for signal-driven trades
        low = _col(row, "Low", close)
        date = str(idx.date())

        # 1) Stop-loss — a standing order, so it triggers intrabar on the low.
        if shares > 0 and entry_price is not None and stop_loss_pct > 0:
            stop_level = entry_price * (1 - stop_loss_pct)
            if low <= stop_level:
                fill = stop_level * (1 - slip)
                cash += shares * fill - commission
                trades.append({"date": date, "action": "STOP", "price": round(fill, 2),
                               "shares": round(shares, 2),
                               "pnl": round((fill - entry_price) * shares, 2)})
                shares = 0.0
                entry_price = None

        # 2) Act on the PREVIOUS bar's signal, at this bar's open (no look-ahead).
        if prev_signal == 1 and shares == 0 and close > 0:
            fill = open_ * (1 + slip)
            invest = cash * position_size_pct
            qty = invest / fill if fill > 0 else 0.0
            if qty > 0:
                shares = qty
                cash -= qty * fill + commission
                entry_price = fill
                trades.append({"date": date, "action": "BUY", "price": round(fill, 2),
                               "shares": round(qty, 2)})
        elif prev_signal == -1 and shares > 0:
            fill = open_ * (1 - slip)
            cash += shares * fill - commission
            trades.append({"date": date, "action": "SELL", "price": round(fill, 2),
                           "shares": round(shares, 2),
                           "pnl": round((fill - entry_price) * shares, 2)})
            shares = 0.0
            entry_price = None

        # 3) Mark to market on the close.
        equity = cash + shares * close
        equity_curve.append({"date": date, "value": round(equity, 2)})
        if shares > 0:
            bars_in_market += 1

        prev_signal = int(row["signal"]) if ("signal" in row and not pd.isna(row["signal"])) else 0

    final_capital = equity_curve[-1]["value"] if equity_curve else float(initial_capital)
    total_return = (final_capital - initial_capital) / initial_capital * 100 if initial_capital else 0.0
    closed = [t for t in trades if "pnl" in t]
    win_rate = sum(1 for t in closed if t["pnl"] > 0) / len(closed) * 100 if closed else 0.0
    exposure = bars_in_market / len(rows) if rows else 0.0

    return {
        "final_capital": round(final_capital, 2),
        "total_return_pct": round(total_return, 2),
        "num_trades": len(closed),
        "win_rate_pct": round(win_rate, 2),
        "trades": trades,
        "equity_curve": equity_curve,
        "exposure": exposure,
    }


def simulate_buy_hold(df: pd.DataFrame, position_size_pct: float, initial_capital: float,
                      slippage_bps: float = 5.0, commission: float = 0.0) -> list:
    """Buy at the first bar's open (with slippage), hold to the end — deploying the
    SAME fraction of capital as the strategy, so it's an apples-to-apples baseline."""
    slip = slippage_bps / 10_000.0
    if df.empty:
        return []
    first = df.iloc[0]
    entry = (_col(first, "Open", _col(first, "Close", 0.0))) * (1 + slip)
    invest = initial_capital * position_size_pct
    qty = invest / entry if entry > 0 else 0.0
    cash = initial_capital - qty * entry - commission
    curve = []
    for idx, row in df.iterrows():
        equity = cash + qty * _col(row, "Close", 0.0)
        curve.append({"date": str(idx.date()), "value": round(equity, 2)})
    return curve


def run(strategy_key: str, params: dict, symbol: str, period: str,
        stop_loss_pct: float, position_size_pct: float, initial_capital: float,
        slippage_bps: float = 5.0, commission: float = 0.0) -> dict:
    strategy = strategies.get(strategy_key)
    df = get_historical_data(symbol, period=period)
    df = strategy.signals(df, params).dropna(subset=["signal"])

    sim = simulate_trades(df, stop_loss_pct, position_size_pct, initial_capital,
                          slippage_bps, commission)
    bh_curve = simulate_buy_hold(df, position_size_pct, initial_capital,
                                 slippage_bps, commission)

    strat_metrics = metrics.compute(sim["equity_curve"], exposure=sim["exposure"],
                                    periods_per_year=TRADING_DAYS)
    bench_metrics = metrics.compute(bh_curve, exposure=1.0, periods_per_year=TRADING_DAYS)
    vs = metrics.vs_benchmark(strat_metrics, bench_metrics)

    # Prefer per-request indicators_for(params) when the strategy provides it
    # (e.g. composite). Fall back to static INDICATORS for legacy strategies.
    if callable(getattr(strategy, "indicators_for", None)):
        indicators_meta = strategy.indicators_for(params)
    else:
        indicators_meta = getattr(strategy, "INDICATORS", [])

    indicator_keys = [ind["key"] for ind in indicators_meta]
    price_history = []
    for idx, row in df.iterrows():
        entry = {
            "date":   str(idx.date()),
            "close":  round(float(row["Close"]), 2),
            "signal": int(row["signal"]) if not pd.isna(row["signal"]) else 0,
        }
        for k in indicator_keys:
            if k in df.columns:
                v = row[k]
                entry[k] = round(float(v), 2) if not pd.isna(v) else None
        price_history.append(entry)

    return {
        "symbol":            symbol,
        "strategy":          strategy.KEY,
        "strategy_name":     strategy.NAME,
        "params":            params,
        "indicators":        indicators_meta,
        "initial_capital":   initial_capital,
        # Headline (kept for backward compatibility with the existing UI):
        "final_capital":     sim["final_capital"],
        "total_return_pct":  sim["total_return_pct"],
        "num_trades":        sim["num_trades"],
        "win_rate_pct":      sim["win_rate_pct"],
        "trades":            sim["trades"],
        "price_history":     price_history,
        # Honest additions:
        "metrics":           strat_metrics,
        "equity_curve":      sim["equity_curve"],
        "benchmark": {
            "label": f"Buy & Hold {symbol}",
            "equity_curve": bh_curve,
            **bench_metrics,
        },
        "vs_benchmark":      vs,
        "costs": {"slippage_bps": slippage_bps, "commission": commission},
    }
