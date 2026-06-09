// Backtest tab — loads strategy registry, renders param cells dynamically,
// POSTs to /api/backtest, renders results.

let strategiesData = [];
let btEquityChart = null;

// Formatting helpers
const btSigned = (v) => (v == null || isNaN(v)) ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(1) + "%";
const btPct = (v) => (v == null || isNaN(v)) ? "—" : Number(v).toFixed(1) + "%";
const btNum = (v) => (v == null || isNaN(v)) ? "—" : Number(v).toFixed(2);

function renderStrategyParams() {
  const select = document.getElementById("bt-strategy");
  const strategy = strategiesData.find((s) => s.key === select.value);
  const container = document.getElementById("bt-strategy-params");
  const desc = document.getElementById("bt-strategy-desc");

  if (!strategy) {
    container.innerHTML = "";
    if (desc) desc.textContent = "";
    return;
  }

  if (desc) desc.textContent = (strategy.description || "").toUpperCase();

  // Footer (backtest tab)
  const btSysStrat = document.getElementById("bt-sys-strategy");
  if (btSysStrat)
    btSysStrat.textContent = (strategy.name || strategy.key).toUpperCase();

  // Strategies that opt into the builder UI render a custom editor instead
  // of the flat param inputs. The custom builder owns its own DOM inside
  // `container` and exposes `readCompositeParams()` for submit serialization.
  // We override `display:contents` so the builder spans the full row of the
  // surrounding grid (`.param-bar`) instead of trying to fit a column.
  if (strategy.builder && typeof window.renderCompositeBuilder === "function") {
    container.innerHTML = "";
    container.classList.add("param-cell-group--builder");
    window.renderCompositeBuilder(container);
    return;
  }
  container.classList.remove("param-cell-group--builder");

  container.innerHTML = (strategy.params || [])
    .map((p, i) => {
      const isNumeric = p.type === "int" || p.type === "float";
      const inputType = isNumeric ? "number" : "text";
      const step = p.type === "int" ? "1" : p.type === "float" ? "0.01" : "";
      const num = String(i + 3).padStart(2, "0"); // continues 03, 04...
      return `
<label class="param-cell">
  <span class="param-cell__num">${num}</span>
  <span class="param-cell__name">${escapeHtml(p.label).toUpperCase()}</span>
  <input type="${inputType}"
         name="param_${escapeHtml(p.key)}"
         value="${escapeHtml(String(p.default))}"
         ${p.min !== undefined ? `min="${p.min}"` : ""}
         ${p.max !== undefined ? `max="${p.max}"` : ""}
         ${step ? `step="${step}"` : ""}
         class="param-cell__input"
         required />
</label>`;
    })
    .join("");
}

async function loadStrategies() {
  try {
    const res = await fetch("/api/strategies");
    if (!res.ok) throw new Error("Failed to load strategies");
    const json = await res.json();
    strategiesData = json.strategies || [];
  } catch (e) {
    strategiesData = [];
  }

  const select = document.getElementById("bt-strategy");
  if (!select) return;
  select.innerHTML = strategiesData
    .map(
      (s) =>
        `<option value="${escapeHtml(s.key)}">${escapeHtml(s.name).toUpperCase()}</option>`,
    )
    .join("");
  select.addEventListener("change", renderStrategyParams);
  renderStrategyParams();
}

function renderCompareGrid(data) {
  const wrap = document.getElementById("bt-compare-grid");
  if (!wrap) return;
  const m = data.metrics || {};
  const b = data.benchmark || {};
  const cmp = [
    ["TOTAL RETURN", btSigned(m.total_return_pct), btSigned(b.total_return_pct)],
    ["CAGR", btSigned(m.cagr_pct), btSigned(b.cagr_pct)],
    ["MAX DRAWDOWN", "−" + btPct(m.max_drawdown_pct), "−" + btPct(b.max_drawdown_pct)],
    ["SHARPE", btNum(m.sharpe), btNum(b.sharpe)],
    ["SORTINO", btNum(m.sortino), btNum(b.sortino)],
    ["VOLATILITY", btPct(m.volatility_pct), btPct(b.volatility_pct)],
    ["EXPOSURE", m.exposure_pct != null ? btPct(m.exposure_pct) : "—", "100.0%"],
  ];
  const cmpRows = cmp.map(([k, s, h]) =>
    `<div class="bt-cmp-row"><span class="bt-cmp-k">${k}</span><span class="bt-cmp-s">${s}</span><span class="bt-cmp-h">${h}</span></div>`).join("");
  const costs = data.costs || {};
  const extra = [
    ["WIN RATE", data.win_rate_pct != null ? data.win_rate_pct.toFixed(0) + "%" : "—"],
    ["# TRADES", String(data.num_trades)],
    ["FINAL CAPITAL", fmtDollar(data.final_capital)],
    ["COSTS", (costs.slippage_bps != null ? costs.slippage_bps : 0) + " bps/fill"],
  ];
  const extraRows = extra.map(([k, v]) =>
    `<div class="bt-cmp-row bt-cmp-row--single"><span class="bt-cmp-k">${k}</span><span class="bt-cmp-s">${v}</span></div>`).join("");
  wrap.innerHTML = `
    <div class="bt-cmp-row bt-cmp-head"><span class="bt-cmp-k"></span><span class="bt-cmp-s">STRATEGY</span><span class="bt-cmp-h">BUY &amp; HOLD</span></div>
    ${cmpRows}
    <div class="bt-cmp-sep"></div>
    ${extraRows}`;
}

function renderBtEquityChart(curve, benchCurve) {
  const canvas = document.getElementById("bt-equity-chart");
  if (!canvas || !curve || curve.length < 2) return;
  if (btEquityChart) { btEquityChart.destroy(); btEquityChart = null; }
  const css = getComputedStyle(document.documentElement);
  const green = css.getPropertyValue("--green").trim();
  const blue = css.getPropertyValue("--blue").trim();
  const inkDim = css.getPropertyValue("--ink-dim").trim();
  btEquityChart = new Chart(canvas, {
    type: "line",
    data: {
      labels: curve.map((p) => p.date),
      datasets: [
        { label: "STRATEGY", data: curve.map((p) => p.value), borderColor: green, borderWidth: 2, pointRadius: 0, tension: 0.2, fill: false },
        {
          label: "BUY & HOLD",
          data: (benchCurve && benchCurve.length === curve.length) ? benchCurve.map((p) => p.value) : curve.map(() => null),
          borderColor: blue, borderWidth: 1.5, borderDash: [5, 4], pointRadius: 0, tension: 0.2, fill: false,
        },
      ],
    },
    options: {
      animation: false, responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => " $" + Number(ctx.raw).toLocaleString(undefined, { maximumFractionDigits: 0 }) } },
      },
      scales: {
        x: { ticks: { color: inkDim, font: { family: "JetBrains Mono", size: 10 }, maxTicksLimit: 8 }, grid: { color: "#1a1a18" } },
        y: { ticks: { color: inkDim, font: { family: "JetBrains Mono", size: 10 }, callback: (v) => "$" + Number(v).toLocaleString() }, grid: { color: "#1a1a18" } },
      },
    },
  });
}

function renderBacktestResults(data) {
  const results = document.getElementById("bt-results");
  results.style.display = "";  // reveal — CSS .tab-stack handles flex column + 20px gap

  const m = data.metrics || {};
  const vs = data.vs_benchmark || {};

  // Headline: strategy return
  const ret = data.total_return_pct;
  const retEl = document.getElementById("bt-total-return");
  retEl.textContent = (ret >= 0 ? "+" : "") + ret.toFixed(2) + "%";
  retEl.className = "cell__big dot-matrix " + (ret >= 0 ? "up" : "down");

  // The verdict: excess return vs buy & hold
  const excess = vs.excess_return_pct;
  const vsEl = document.getElementById("bt-vs-benchmark");
  if (vsEl) {
    vsEl.textContent = btSigned(excess);
    vsEl.className = "cell__big dot-matrix " + (vs.beats_benchmark ? "up" : "down");
  }

  // Max drawdown (always a loss → shown negative, red)
  const ddEl = document.getElementById("bt-maxdd");
  if (ddEl) {
    ddEl.textContent = m.max_drawdown_pct != null ? "−" + Number(m.max_drawdown_pct).toFixed(1) + "%" : "—";
    ddEl.className = "cell__big dot-matrix down";
  }

  // Sharpe (green if ≥1, red if negative)
  const shEl = document.getElementById("bt-sharpe");
  if (shEl) {
    shEl.textContent = btNum(m.sharpe);
    shEl.className = "cell__big dot-matrix " + (m.sharpe >= 1 ? "up" : m.sharpe < 0 ? "down" : "");
  }

  // Plain-language verdict in the comparison panel header
  const verdictEl = document.getElementById("bt-verdict");
  if (verdictEl) {
    const beat = vs.beats_benchmark;
    const ddBetter = (vs.dd_improvement_pct || 0) > 0;
    verdictEl.textContent = beat
      ? `BEAT BUY & HOLD BY ${btSigned(excess)} · ${ddBetter ? "SMALLER" : "LARGER"} DRAWDOWN`
      : `LOST TO BUY & HOLD BY ${btPct(Math.abs(excess))} · ${ddBetter ? "SMALLER" : "LARGER"} DRAWDOWN`;
    verdictEl.className = "panel__sub " + (beat ? "up" : "down");
  }

  renderCompareGrid(data);
  renderBtEquityChart(data.equity_curve, (data.benchmark || {}).equity_curve);

  buildLegend(document.getElementById("bt-legend"), indicatorsFor(data));
  buildChart("bt-chart", data);
  buildOscillatorChart("bt-osc-chart", "bt-osc-wrap", data);
  renderTrades("bt-trade-log", data.trades, 100);

  // Footer last-run stamp
  const btSysLast = document.getElementById("bt-sys-lastrun");
  if (btSysLast) btSysLast.textContent = new Date().toLocaleTimeString();
}

function syncBacktestFooterFromForm() {
  const form = document.getElementById("backtest-form");
  if (!form) return;
  const periodEl = document.getElementById("bt-sys-period");
  if (periodEl && form.period)
    periodEl.textContent = form.period.value.toUpperCase();
}

loadStrategies();
syncBacktestFooterFromForm();
document
  .getElementById("backtest-form")
  ?.addEventListener("change", syncBacktestFooterFromForm);

const form = document.getElementById("backtest-form");
if (form) {
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = document.getElementById("bt-run");
    const status = document.getElementById("bt-status");

    const strategyKey = form.strategy.value;
    const strategy = strategiesData.find((s) => s.key === strategyKey);
    if (!strategy) {
      if (status) status.textContent = "NO STRATEGY SELECTED.";
      return;
    }

    let params;
    if (strategy.builder && typeof window.readCompositeParams === "function") {
      params = window.readCompositeParams();
    } else {
      params = {};
      for (const p of strategy.params || []) {
        const input = form.elements[`param_${p.key}`];
        if (!input) continue;
        const v = input.value;
        params[p.key] =
          p.type === "int" ? parseInt(v, 10) : p.type === "float" ? Number(v) : v;
      }
    }

    const payload = {
      strategy: strategyKey,
      symbol: form.symbol.value.trim().toUpperCase(),
      params: params,
      stop_loss_pct: Number(form.stop_loss_pct.value),
      position_size_pct: Number(form.position_size_pct.value),
      slippage_bps: Number(form.slippage_bps.value),
      initial_capital: Number(form.initial_capital.value),
      period: form.period.value,
    };

    btn.disabled = true;
    btn.textContent = "▸ RUNNING…";
    if (status) status.textContent = "";

    try {
      const res = await fetch("/api/backtest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const err = await res
          .json()
          .catch(() => ({ detail: "Request failed" }));
        throw new Error(err.detail || "Request failed");
      }
      const data = await res.json();
      renderBacktestResults(data);
      if (status)
        status.textContent = `DONE · ${data.num_trades} TRADES SIMULATED`;
    } catch (err) {
      if (status) status.textContent = "ERROR: " + err.message;
    } finally {
      btn.disabled = false;
      btn.textContent = "▸ RUN BACKTEST";
    }
  });
}
