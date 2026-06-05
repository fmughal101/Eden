"""
Live trading bot
================
Polls market data on an interval, runs a strategy, places paper orders
through Alpaca, and writes state to data.json for the dashboard.
"""

import json
import logging
import time
from datetime import datetime

import pandas as pd

from config import (
    ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL,
    SYMBOL, SHORT_WINDOW, LONG_WINDOW,
    POSITION_SIZE_PCT, STOP_LOSS_PCT, CHECK_INTERVAL, DATA_FILE,
)
from data import get_historical_data
import strategies

log = logging.getLogger(__name__)

_trade_log: list = []


# ─────────────────────────────────────────────
#  STATE EXPORT
# ─────────────────────────────────────────────

def save_state(status, df, current_price, shares_held,
               portfolio_value, initial_capital, signal, strategy_meta):
    history_df = df.tail(120)
    indicator_keys = [ind["key"] for ind in strategy_meta.get("indicators", [])]

    price_history = []
    for idx, row in history_df.iterrows():
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

    state = {
        "status":            status,
        "symbol":            SYMBOL,
        "portfolio_value":   round(portfolio_value, 2),
        "initial_capital":   round(initial_capital, 2),
        "current_price":     round(current_price, 2) if current_price else None,
        "shares_held":       shares_held,
        "signal":            signal,
        "strategy":          strategy_meta.get("name"),
        "indicators":        strategy_meta.get("indicators", []),
        "stop_loss_pct":     round(STOP_LOSS_PCT * 100, 1),
        "position_size_pct": round(POSITION_SIZE_PCT * 100, 1),
        "trades":            _trade_log[-20:],
        "price_history":     price_history,
        "last_updated":      datetime.now().isoformat(),
    }

    with open(DATA_FILE, "w") as f:
        json.dump(state, f, indent=2)
    log.info(f"State saved → {DATA_FILE}")


def log_trade(action, price, shares, pnl=None):
    _trade_log.append({
        "date":   datetime.now().strftime("%Y-%m-%d %H:%M"),
        "action": action,
        "price":  round(price, 2),
        "shares": shares,
        "pnl":    round(pnl, 2) if pnl is not None else None,
    })


# ─────────────────────────────────────────────
#  ALPACA INTERFACE
# ─────────────────────────────────────────────

def get_alpaca_client():
    try:
        import alpaca_trade_api as tradeapi
    except ImportError:
        raise ImportError("Run: pip install alpaca-trade-api")
    return tradeapi.REST(ALPACA_API_KEY, ALPACA_SECRET_KEY,
                         ALPACA_BASE_URL, api_version="v2")


def get_portfolio_value(api):
    return float(api.get_account().portfolio_value)


def get_current_position(api, symbol):
    try:
        return int(api.get_position(symbol).qty)
    except Exception:
        return 0


def place_order(api, symbol, qty, side):
    if qty <= 0:
        return None
    log.info(f"Order: {side.upper()} {qty} {symbol}")
    return api.submit_order(symbol=symbol, qty=qty, side=side,
                            type="market", time_in_force="day")


# ─────────────────────────────────────────────
#  MAIN LOOP
# ─────────────────────────────────────────────

def run(strategy_key: str = "sma_crossover", params: dict | None = None):
    if ALPACA_API_KEY == "YOUR_API_KEY_HERE":
        log.error("Add your Alpaca API keys first (config.py)!")
        return

    strategy = strategies.get(strategy_key)
    if params is None:
        # Use config.py defaults if the strategy is SMA crossover, else plugin defaults
        if strategy_key == "sma_crossover":
            params = {"short_window": SHORT_WINDOW, "long_window": LONG_WINDOW}
        else:
            params = {p["key"]: p["default"] for p in strategy.PARAMS}

    strategy_meta = {"name": strategy.NAME, "indicators": strategy.INDICATORS}

    api = get_alpaca_client()
    initial_capital = get_portfolio_value(api)
    log.info(f"Bot started | {SYMBOL} | strategy={strategy.NAME} | params={params}")

    while True:
        try:
            df = get_historical_data(SYMBOL, period="3mo")
            df = strategy.signals(df, params)
            latest = df.iloc[-1]
            price  = float(latest["Close"])
            signal = int(latest["signal"]) if not pd.isna(latest["signal"]) else 0
            current_shares = get_current_position(api, SYMBOL)
            portfolio = get_portfolio_value(api)

            # Stop-loss check
            if current_shares > 0:
                try:
                    entry = float(api.get_position(SYMBOL).avg_entry_price)
                    if price <= entry * (1 - STOP_LOSS_PCT):
                        log.warning(f"STOP-LOSS @ ${price:.2f}")
                        place_order(api, SYMBOL, current_shares, "sell")
                        log_trade("STOP", price, current_shares,
                                  pnl=(price - entry) * current_shares)
                        save_state("running", df, price, 0, portfolio,
                                   initial_capital, signal, strategy_meta)
                        time.sleep(CHECK_INTERVAL)
                        continue
                except Exception:
                    pass

            if signal == 1 and current_shares == 0:
                qty = int((portfolio * POSITION_SIZE_PCT) / price)
                place_order(api, SYMBOL, qty, "buy")
                log_trade("BUY", price, qty)

            elif signal == -1 and current_shares > 0:
                place_order(api, SYMBOL, current_shares, "sell")
                try:
                    entry = float(api.get_position(SYMBOL).avg_entry_price)
                    log_trade("SELL", price, current_shares,
                              pnl=(price - entry) * current_shares)
                except Exception:
                    log_trade("SELL", price, current_shares)

            save_state("running", df, price,
                       get_current_position(api, SYMBOL),
                       portfolio, initial_capital, signal, strategy_meta)

        except KeyboardInterrupt:
            log.info("Bot stopped.")
            break
        except Exception as e:
            log.error(f"Loop error: {e}")

        time.sleep(CHECK_INTERVAL)
