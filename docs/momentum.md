# Momentum Portfolio — User Guide

The **MOMENTUM** tab runs a validated, multi-asset *dual-momentum* strategy: it holds
whatever in a small basket is trending hardest, and steps aside into bonds when nothing
is. It's the first strategy in Eden that honestly beat buy-and-hold on a risk-adjusted
basis — similar-or-better returns with roughly **half the drawdown** over ~20 years.

> ⚠️ Educational tool, not financial advice. Backtests describe the past; the future can
> differ. See [Honest caveats](#honest-caveats).

---

## Quick start

1. Open the **MOMENTUM** tab. It auto-runs a ~20-year backtest on load.
2. Read **HOLD NOW** — the exact positions the strategy says to hold this month.
3. Skim the headline stats and the **STRATEGY COMPARISON** table (you vs the benchmarks).
4. (Optional) tweak the parameters and hit **RUN BACKTEST** to re-test.
5. To trade it on paper, see [Going live](#going-live).

---

## The strategy, in plain English

Every month, the strategy:

1. **Measures momentum** — the trailing return (default: last **12 months**) of each asset
   in the risk basket: **SPY** (US stocks), **QQQ** (US tech), **EFA** (developed
   international), **GLD** (gold).
2. **Picks the leaders** — holds the **top N** (default: **2**) by that trailing return,
   equally weighted.
3. **Applies an absolute-momentum filter** — if one of those leaders actually has a
   *negative* trailing return (i.e. it's "leading" only because everything else is worse),
   that slot goes to the **safe asset, AGG** (US bonds) instead of risking it.
4. **Rebalances** to those targets, holding until next month.

So in a healthy bull market you might hold QQQ + SPY; in a chop you might hold GLD + bonds;
in a crash the filter pulls you toward AGG. That defensive step is where most of the
drawdown protection comes from (it sidestepped much of 2008).

---

## Parameters

| Control | Default | What it means | When to change it |
|---|---|---|---|
| **BASKET** | `SPY·QQQ·EFA·GLD → AGG` | The risk assets it rotates among, and the safe asset (`AGG`) it retreats to. Fixed in the UI for now. | — |
| **TOP N** | `2` | How many basket assets to hold at once. `1` = most concentrated/aggressive (chase the single strongest); `2–3` = more diversified and smoother. | Lower N for punchier returns + bigger swings; higher N for smoother. |
| **LOOKBACK** | `12` months | The window used to measure momentum. Shorter = more reactive (trades more, whipsaws more); longer = steadier (slower to flip). | 6–12 is the robust range; all behaved well in testing. |
| **SLIPPAGE** | `5` bps | Assumed trading cost per fill, in basis points (5 bps = 0.05%). Alpaca equities are commission-free, so this models spread/slippage. Turnover is low (monthly), so costs are minor. | Raise it to stress-test against worse fills. |

> Tip: the strategy is **robust across these settings** — in testing, every combination of
> lookback (6/9/12) × Top-N (1/2/3) beat SPY's risk-adjusted return. You're tuning
> flavor, not hunting for a magic setting. The defaults (Top-2, 12-month) are a solid,
> well-diversified baseline.

---

## Reading the results

### HOLD NOW
The current target allocation (e.g. `QQQ 50%  GLD 50%`), as of the latest month-end. Green
chips = risk assets; blue = the safe asset (AGG). This is the live signal you'd act on.
The **PREVIEW REBALANCE (PAPER)** button turns it into actual orders — see [Going live](#going-live).

### Headline cells
For the recommended **Top-N** strategy:
- **CAGR** — annualized return (the "speed" of compounding).
- **MAX DRAWDOWN** — the worst peak-to-trough drop you'd have endured (the "pain").
- **SHARPE** — return per unit of risk (higher = better; >1 is good).
- **LESS DRAWDOWN vs SPY** — how much smaller the worst drop was than just holding SPY.
  This is the strategy's headline advantage.

### Strategy comparison table
Every strategy (rows) across every metric (columns), Top-N highlighted. Use it to judge
whether the strategy actually earns its complexity vs the simple benchmarks. See
[Metrics glossary](#metrics-glossary) and [The benchmarks](#the-benchmarks).

### Growth of $10,000 (log scale)
Equity curves for all four strategies. **Log scale** means equal *percentage* moves look
equal in height — the honest way to view 20 years of compounding (a straight line = steady
growth). Steeper = faster growth; flatter dips = smaller drawdowns.

---

## Metrics glossary

| Metric | Plain meaning | Rule of thumb |
|---|---|---|
| **Total Return** | Cumulative gain over the whole period. | Bigger is better, but ignores risk. |
| **CAGR** | Annualized (compound) growth rate. | The honest "per-year" number. |
| **Max Drawdown** | Worst peak-to-trough loss. | Smaller is better; this is what makes a strategy *stickable*. |
| **Sharpe** | Return per unit of total volatility. | >1 good, >0.5 ok, <0 bad. |
| **Sortino** | Like Sharpe but only penalizes *downside* volatility. | Usually higher than Sharpe; >1 is good. |
| **Volatility** | Annualized standard deviation of returns (how bumpy). | Lower = calmer ride. |

All metrics are computed *after costs* and on a true mark-to-market equity curve, by the
shared `metrics.py` engine — the same ruler used everywhere else in Eden.

---

## The benchmarks

The comparison always includes three baselines so you can see if the strategy adds value:

- **Buy & Hold SPY** — 100% S&P 500, held the whole time. "Just holding the market."
- **60/40 SPY-AGG** — the classic 60% stocks / 40% bonds, rebalanced monthly. The standard
  "balanced portfolio" baseline.
- **Dual Momentum (GEM)** — a classic single-pair momentum (SPY vs international, else bonds).
  Included as a reference; in testing it *underperformed*, which is itself instructive —
  not every momentum recipe works.

A strategy is only worth running if it beats these **risk-adjusted** (better Sharpe / smaller
drawdown), not just on raw return.

---

## Going live

The strategy trades on your **Alpaca paper** account, manually, with a preview-first flow.

1. **Add paper keys.** Create a file named `.env` next to `server.py`:
   ```
   ALPACA_API_KEY=your_paper_key
   ALPACA_SECRET_KEY=your_paper_secret
   ```
   (The client is hard-coded to `paper=True` — it cannot touch a live account.)
2. **Preview.** In the MOMENTUM tab, click **PREVIEW REBALANCE (PAPER)**. It reads your
   account and shows the *exact* orders needed to reach target (sells first, then buys;
   small drifts are left alone via a no-trade band).
3. **Execute.** Click **EXECUTE ON PAPER** and confirm. It submits the orders. (Until keys
   are configured, this button stays disabled and the preview assumes a fresh $10k cash
   account so you can still see what it *would* do.)
4. **Cadence.** Run it **once a month** — the strategy only changes at month-end. Doing it
   more often just adds costs.

---

## Honest caveats

- **Regime dependence.** The *return* edge leans partly on QQQ's huge 2005–2026 tech run —
  drop QQQ from the basket and it trails SPY on raw return (though it still wins on
  drawdown). The durable, robust edge is **risk reduction**, not always out-returning SPY.
- **It won't beat a raging bull.** In a straight bull market it roughly *ties* buy-and-hold;
  its value shows up across a full cycle, especially in crashes.
- **One dataset.** It's a single ~20-year US-centric history. Robust within that (param
  sweep + both halves held up), but the future is not guaranteed to rhyme.
- **Monthly, same-close timing.** Signals are computed at month-end closes and acted on
  around then; a tiny timing assumption that barely matters at monthly cadence.
- **Not advice.** This is a research/education tool. Paper-trade it, understand it, and only
  ever risk what you choose to.

---

## Under the hood

- **Engine:** `portfolio.py` — weight generators (`topn_weights`, `gem_weights`, …),
  `simulate` (daily mark-to-market + turnover costs), `backtest_api`, `current_target`.
- **Metrics:** `metrics.py` (shared honest metrics).
- **Live executor:** `momentum_live.py` (`preview` / `execute`, Alpaca paper).
- **API:** `POST /api/momentum/backtest`, `GET /api/momentum/current`,
  `GET /api/momentum/rebalance/preview`, `POST /api/momentum/rebalance/execute`.
- **UI:** `static/js/momentum.js`, the `#tab-momentum` section in `dashboard.html`.
