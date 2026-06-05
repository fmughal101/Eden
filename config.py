"""
Bot configuration
=================
Edit these values to tune the live bot. Backtests in the dashboard ignore
most of these — they accept their own params per request.
"""

from pathlib import Path

# ─── Alpaca credentials ───────────────────────────────────────
ALPACA_API_KEY    = "YOUR_API_KEY_HERE"
ALPACA_SECRET_KEY = "YOUR_SECRET_KEY_HERE"
ALPACA_BASE_URL   = "https://paper-api.alpaca.markets"

# ─── Live bot defaults ────────────────────────────────────────
SYMBOL            = "SPY"
SHORT_WINDOW      = 20
LONG_WINDOW       = 50
POSITION_SIZE_PCT = 0.10
STOP_LOSS_PCT     = 0.03
CHECK_INTERVAL    = 60 * 5

# ─── Files ────────────────────────────────────────────────────
DATA_FILE = Path("data.json")
LOG_FILE  = Path("bot.log")
