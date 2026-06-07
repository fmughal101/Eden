# Eden — Trading Engine

A retro-terminal-styled dashboard for designing, backtesting, and paper-trading
stock strategies — plus an AI-powered ticker research tool that pulls
fundamentals, runs a live web search, and produces a structured bull/bear thesis.

```
┌─ LIVE ─┬─ BACKTEST ─┬─ SIGNALS ─┬─ RESEARCH ─┐
│  bot   │  composite │  webhook  │   claude   │
│ status │   builder  │  journal  │ + websearch│
└────────┴────────────┴───────────┴────────────┘
```

---

## What it does today

**Live trading (paper):** An SMA-crossover bot trades SPY through Alpaca's
paper API. State (portfolio value, positions, recent trades, signals) is
persisted to `data.json` and streamed to the LIVE tab every 10 seconds.

**Backtesting:** Two strategies ship out of the box:

- `sma_crossover` — golden/death cross on two simple moving averages.
- `composite` — a custom-built strategy assembled in the browser: pick
  indicators from a library (SMA, EMA, RSI, MACD, Bollinger, ATR),
  declare ENTRY conditions (BUY when ALL true) and EXIT conditions
  (SELL when ANY true), then run against any Yahoo-listed symbol.
  Indicators render on the chart — price-scale ones (SMA / EMA /
  Bollinger) on the main panel, oscillator-scale ones (RSI / MACD / ATR)
  on a sibling panel below.

**Signal journal:** A `/webhook/tradingview` endpoint accepts authenticated
JSON payloads from TradingView alerts, validates them, and writes every
signal (received, rejected, executed) to a SQLite journal. The SIGNALS tab
renders the log live.

**Ticker research (Claude):** Type a symbol on the RESEARCH tab. The
server pulls yfinance fundamentals + recent headlines and asks Claude
Opus 4.7 (with the built-in `web_search` tool and adaptive thinking) to
produce a structured response: BUY/HOLD/SELL rating, confidence score,
bull/bear bullets, and cited sources. Cached per `(symbol, day)` so repeat
queries cost nothing.

**Dashboard:** A static-asset single-page app served by the same FastAPI
process — no build step, no framework. Live polling at 10s, signals at 5s,
ticker quotes refreshed every 60s. The bottom system bar swaps cells per
tab (e.g. STRATEGY · LATENCY · SIGNAL on LIVE; SOURCE · TODAY · LAST on
SIGNALS).

---

## Goals

- **No Python required to design a strategy.** The composite builder
  should cover ~95% of indicator-based strategies without writing code.
- **One process, no infrastructure.** A single `python server.py` runs
  the dashboard, the backtest engine, the webhook receiver, and the
  research API. No Redis, no message queue, no Celery.
- **Educational by default.** Real prices, real indicators, paper money —
  so you can experiment with strategies and observe outcomes without
  capital at risk.
- **AI as a research aid, not an oracle.** Claude is plumbed in for
  ticker research and (eventually) signal generation, but every output
  carries a "not financial advice" disclaimer and surfaces its sources.

---

## Architecture

```
                ┌─ Browser ─┐
                │ dashboard │
                └─────┬─────┘
                      │ /api/state, /api/backtest, /api/quotes,
                      │ /api/signals, /api/research
                      ▼
        ┌─────────── FastAPI (server.py) ────────────┐
        │                                            │
        │  ┌── strategies/ ──┐  ┌── backtest.py ──┐  │
        │  │ sma_crossover  ─┼──┤ run() simulate  │  │
        │  │ composite       │  │  _trades()       │  │
        │  │ indicators.py   │  └──────────────────┘  │
        │  └─────────────────┘                        │
        │                                              │
        │  ┌── research.py ─┐  ┌── journal.py (SQLite)│
        │  │ Claude API +   │  │ webhook signals log  │
        │  │ web_search     │  └─────────────────────┘│
        │  └────────────────┘                          │
        └──────────┬──────────┬────────────────┬──────┘
                   │          │                │
              yfinance    Anthropic       Alpaca paper
              (prices,     (sonnet/opus)   (live exec)
               news,
               fundamentals)
```

- **Strategies are auto-discovered.** Drop a `.py` file in `strategies/`
  that exposes `KEY`, `NAME`, `PARAMS`, `INDICATORS`, and a `signals(df,
params)` function, and it appears in the backtest dropdown — no
  registration step.
- **The backtest engine is stateless.** `backtest.run()` calls
  `strategy.signals(df, params)` then walks the resulting `signal`
  column through `simulate_trades()`. The same engine powers the
  composite strategy without modification.
- **Frontend is plain JS.** No bundler, no framework. Each tab has its
  own JS file; shared utilities live in `static/js/utils.js`. Chart.js
  is the only external runtime dependency.

---

## Quickstart

### First time on a new machine

```powershell
# 1. Clone the repo
git clone https://github.com/fmughal101/Eden.git
cd Eden

# 2. Create a venv + install deps
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 3. (Optional) set API keys for the AI / webhook features
$env:ANTHROPIC_API_KEY = "sk-ant-..."          # RESEARCH tab — Claude
$env:TV_WEBHOOK_SECRET = "your-shared-secret"   # /webhook/tradingview auth
# Edit config.py to add Alpaca paper keys for the live bot (optional)

# 4. Run the dashboard
python server.py
# → http://localhost:8000
```

For every new terminal session you just need two commands:

```powershell
source .venv/bin/activate
python server.py
```

On macOS / Linux, swap step 2's activate line for `source .venv/bin/activate`
and step 3's `$env:NAME = "value"` for `export NAME="value"`.

The dashboard works without any API keys — you'll just get 503s from
`/api/research`, `/webhook/tradingview` will reject everything, and the
live bot will be offline until you populate `config.py`.

### Subsequent pulls (same machine)

```powershell
cd Eden
git pull
.venv\Scripts\activate
pip install -r requirements.txt   # only needed if requirements changed
python server.py
```

### Persisting your API keys

`$env:NAME = ...` only sets the variable for the current PowerShell
session. To make `ANTHROPIC_API_KEY` survive reboots:

```powershell
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-...", "User")
# Close + reopen the terminal afterwards.
```

### What's persisted vs ignored

`.gitignore` excludes the things that should be local-only:
`.venv/`, `__pycache__/`, `bot.log`, `data.json` (live bot state),
`journal.db` (signal log), `.env*`, and `*.zip`. Pull will not bring
those down — they're regenerated on demand. Your custom Alpaca keys in
`config.py` are NOT git-ignored, so don't commit them as-is. Either
keep `config.py` with placeholder values and load real keys from env
vars, or `git update-index --skip-worktree config.py` after editing.

---

## Project layout

```
Eden/
├── server.py             FastAPI: dashboard host + API + webhooks
├── trading_bot.py        Live SMA bot (Alpaca paper)
├── backtest.py           Strategy-agnostic backtest engine
├── strategies/
│   ├── __init__.py       Auto-discovery registry
│   ├── sma_crossover.py  Reference strategy
│   ├── composite.py      User-built strategy (safe rule evaluator)
│   └── indicators.py     SMA, EMA, RSI, MACD, Bollinger, ATR
├── research.py           Claude-powered ticker research
├── data.py               yfinance wrapper
├── live.py               Live state writer (data.json)
├── journal.py            SQLite signal journal
├── config.py             Bot config (Alpaca keys, defaults)
├── dashboard.html        Single-page UI
└── static/
    ├── dashboard.css     Terminal palette + components
    └── js/
        ├── utils.js              Chart.js helpers + formatters
        ├── tabs.js               Tab switcher
        ├── footer.js             Bottom-bar tab swap
        ├── clock.js              Header clock
        ├── ticker.js             Scrolling ticker tape
        ├── live.js               LIVE tab
        ├── backtest.js           BACKTEST tab
        ├── composite_builder.js  Composite strategy editor
        ├── signals.js            SIGNALS tab
        └── research.js           RESEARCH tab
```

---

## Roadmap

Short-term:

- [ ] Per-oscillator chart panels (RSI and MACD currently share one
      y-axis on the sibling chart — fine alone, cramped together)
- [ ] Save/load custom composite strategies (server-side, named slots)
- [ ] Disable stop-loss with `0` instead of "trigger immediately"
- [ ] Stress-test the composite engine with a 200-period SMA on a 10-year
      dataset
- [ ] Persistent research cache (currently in-memory, lost on reload)
- [ ] `.env` file support so API keys don't have to be re-exported per
      shell

Medium-term:

- [ ] **ML signal generator** — train a model on historical indicator
      features and surface its predictions alongside the rules-based
      signal (per `CLAUDE.md`)
- [ ] **Position sizing improvements** — Kelly, volatility-targeted,
      fixed-risk
- [ ] **Multi-symbol portfolios** — run the same strategy on a basket,
      track aggregate P&L
- [ ] **Strategy comparison view** — run N strategies side-by-side on
      the same period, compare equity curves and KPIs
- [ ] **Real-time price streaming** — replace 60s polling with Alpaca's
      websocket feed
- [ ] **Claude-generated strategy proposals** — describe a hypothesis
      in English, get a composite JSON config back to tweak

Speculative / longer-term:

- [ ] Live trading via Alpaca (gated behind a confirmation modal)
- [ ] Options strategies (covered calls, spreads)
- [ ] Risk dashboard — drawdown, VaR, exposure by sector
- [ ] Mobile-responsive layout
- [ ] Self-host on a tiny VM with a TradingView webhook tunnel

---

## Stack

| Layer    | Tech                                     |
| -------- | ---------------------------------------- |
| Backend  | Python 3.11+, FastAPI, uvicorn           |
| Data     | yfinance (prices + fundamentals + news)  |
| Trading  | Alpaca paper API (`alpaca-py`)           |
| AI       | Anthropic Claude (Opus 4.7) + web_search |
| Storage  | SQLite (journal), JSON file (live state) |
| Frontend | Plain JS, Chart.js, no build step        |
| Fonts    | JetBrains Mono, Departure Mono           |

---

## License

Personal project — no license declared. Not for redistribution. No part
of this code is financial advice.
