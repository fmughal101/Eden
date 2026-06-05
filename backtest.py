"""
Backtest engine
===============
Runs a strategy plugin over historical data and returns results
(trades, return %, win rate, equity curve).
"""

import pandas as pd

from data import get_historical_data
import strategies


def simulate_trades(df: pd.DataFrame, stop_loss_pct: float,
                    position_size_pct: float, initial_capital: float) -> dict:
    """Walk a signal-ready dataframe (with 'signal' column), simulating trades."""
    capital = initial_capital
    shares = 0
    entry_price = None
    trades = []

    for i, row in df.iterrows():
        price = float(row["Close"])
        sig = row["signal"]

        if sig == 1 and shares == 0:
            shares = int((capital * position_size_pct) / price)
            if shares > 0:
                capital -= shares * price
                entry_price = price
                trades.append({"date": str(i.date()), "action": "BUY",
                               "price": round(price, 2), "shares": shares})

        elif shares > 0:
            stop_hit    = entry_price and price <= entry_price * (1 - stop_loss_pct)
            sell_signal = sig == -1
            if sell_signal or stop_hit:
                pnl = (price - entry_price) * shares
                capital += shares * price
                trades.append({"date": str(i.date()),
                               "action": "STOP" if stop_hit else "SELL",
                               "price": round(price, 2), "shares": shares,
                               "pnl": round(pnl, 2)})
                shares = 0
                entry_price = None

    if shares > 0:
        capital += shares * float(df["Close"].iloc[-1])

    total_return = (capital - initial_capital) / initial_capital * 100
    closed = [t for t in trades if "pnl" in t]
    win_rate = sum(1 for t in closed if t["pnl"] > 0) / len(closed) * 100 if closed else 0

    return {
        "final_capital":    round(capital, 2),
        "total_return_pct": round(total_return, 2),
        "num_trades":       len(closed),
        "win_rate_pct":     round(win_rate, 2),
        "trades":           trades,
    }


def run(strategy_key: str, params: dict, symbol: str, period: str,
        stop_loss_pct: float, position_size_pct: float,
        initial_capital: float) -> dict:
    strategy = strategies.get(strategy_key)
    df = get_historical_data(symbol, period=period)
    df = strategy.signals(df, params).dropna(subset=["signal"])

    sim = simulate_trades(df, stop_loss_pct, position_size_pct, initial_capital)

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
        "final_capital":     sim["final_capital"],
        "total_return_pct":  sim["total_return_pct"],
        "num_trades":        sim["num_trades"],
        "win_rate_pct":      sim["win_rate_pct"],
        "trades":            sim["trades"],
        "price_history":     price_history,
    }
