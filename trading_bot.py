"""
Trading Bot — entry point
=========================
Thin CLI dispatcher. Real logic lives in:
    config.py        — settings
    data.py          — yfinance fetcher
    strategies/      — strategy plugins
    backtest.py      — backtest engine
    live.py          — live Alpaca paper-trading loop
    server.py        — FastAPI dashboard + webhook

Usage:
    python trading_bot.py              # backtest first, then live if profitable
    python trading_bot.py backtest     # backtest only
    python trading_bot.py live         # live only

For the dashboard:
    python server.py
"""

import logging
import sys

import backtest
import live
from config import (
    SYMBOL, SHORT_WINDOW, LONG_WINDOW,
    STOP_LOSS_PCT, POSITION_SIZE_PCT, LOG_FILE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)


def cli_backtest() -> dict:
    result = backtest.run(
        strategy_key="sma_crossover",
        params={"short_window": SHORT_WINDOW, "long_window": LONG_WINDOW},
        symbol=SYMBOL,
        period="2y",
        stop_loss_pct=STOP_LOSS_PCT,
        position_size_pct=POSITION_SIZE_PCT,
        initial_capital=10_000.0,
    )
    print("\n" + "=" * 45)
    print("  BACKTEST RESULTS")
    print("=" * 45)
    for k, v in result.items():
        if k not in ("trades", "price_history", "indicators", "params"):
            print(f"  {k:<26} {v}")
    print("=" * 45 + "\n")
    return result


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else None

    if mode == "backtest":
        cli_backtest()
    elif mode == "live":
        live.run()
    else:
        print("\nRunning backtest first...\n")
        results = cli_backtest()
        if results["total_return_pct"] > 0:
            print("Backtest profitable — starting bot...\n")
            live.run()
        else:
            print("Backtest unprofitable. Tune parameters before trading.\n")
