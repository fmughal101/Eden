// MOMENTUM tab — the validated multi-asset dual-momentum strategy vs benchmarks.
// Lazy-loads on first tab open; re-runs on form submit. Reuses the honest metrics
// from the backend (metrics.py) — same ruler as the single-asset backtester.

(function () {
  const tab = document.getElementById("tab-momentum");
  if (!tab) return;

  let momChart = null;
  let loaded = false;

  const signed = (v) => (v == null || isNaN(v)) ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(1) + "%";
  const pct = (v) => (v == null || isNaN(v)) ? "—" : Number(v).toFixed(1) + "%";
  const num = (v) => (v == null || isNaN(v)) ? "—" : Number(v).toFixed(2);
  const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");

  function readParams() {
    const f = document.getElementById("momentum-form");
    return {
      top_n: Number(f.top_n.value) || 2,
      lookback: Number(f.lookback.value) || 12,
      cost_bps: Number(f.cost_bps.value) || 5,
    };
  }

  function setCell(id, text, c) {
    const el = document.getElementById(id);
    if (el) { el.textContent = text; el.className = "cell__big dot-matrix " + (c || ""); }
  }

  async function runMomentum() {
    const status = document.getElementById("mom-status");
    const btn = document.getElementById("mom-run");
    if (btn) { btn.disabled = true; btn.textContent = "▸ RUNNING…"; }
    if (status) status.textContent = "CRUNCHING ~20 YEARS…";
    try {
      const res = await fetch("/api/momentum/backtest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(readParams()),
      });
      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        throw new Error(e.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      renderMomentum(data);
      if (status) status.textContent = `DONE · ${data.start} → ${data.end}`;
    } catch (e) {
      if (status) status.textContent = "ERROR: " + e.message;
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "▸ RUN BACKTEST"; }
    }
  }

  function renderMomentum(data) {
    const results = document.getElementById("mom-results");
    results.style.display = "flex";
    results.style.flexDirection = "column";
    results.style.gap = "20px";

    const strat = data.strategies || [];
    const top = strat[0] || {};
    const m = top.metrics || {};
    const spy = strat.find((s) => /Buy & Hold/.test(s.name)) || {};
    const sm = spy.metrics || {};

    // CURRENT SIGNAL — what to hold right now
    const cur = data.current_target || {};
    const curEl = document.getElementById("mom-current");
    const isCash = !cur.AGG && Object.keys(cur).every((k) => k === "AGG");
    curEl.innerHTML = Object.keys(cur).length
      ? Object.entries(cur).map(([s, w]) =>
          `<span class="mom-chip ${s === "AGG" ? "mom-chip--safe" : ""}">${escapeHtml(s)} <b>${w}%</b></span>`).join("")
      : `<span class="mom-chip mom-chip--safe">CASH / BONDS</span>`;
    const asof = document.getElementById("mom-asof");
    if (asof) asof.textContent = "AS OF " + (data.current_date || "—") + " · TOP-" + (data.params ? data.params.top_n : "");

    // Headline cells (the recommended Top-N strategy)
    setCell("mom-cagr", pct(m.cagr_pct), m.cagr_pct >= 0 ? "up" : "down");
    setCell("mom-maxdd", "−" + pct(m.max_drawdown_pct), "down");
    setCell("mom-sharpe", num(m.sharpe), m.sharpe >= 1 ? "up" : m.sharpe < 0 ? "down" : "");
    const ddImp = (sm.max_drawdown_pct || 0) - (m.max_drawdown_pct || 0); // +ve = strategy smaller DD
    setCell("mom-vs", (ddImp >= 0 ? "−" : "+") + pct(Math.abs(ddImp)), ddImp >= 0 ? "up" : "down");

    renderCompare(strat);
    renderChart(strat);

    const periodEl = document.getElementById("mom-period");
    if (periodEl) periodEl.textContent = (data.start || "") + " → " + (data.end || "");
  }

  function renderCompare(strat) {
    const wrap = document.getElementById("mom-compare");
    if (!wrap) return;
    const rows = strat.map((s, i) => {
      const m = s.metrics;
      return `<tr class="${i === 0 ? "mom-row--hl" : ""}">
        <td class="copy-td copy-td--name">${escapeHtml(s.name)}</td>
        <td class="copy-td ${cls(m.cagr_pct)}">${signed(m.cagr_pct)}</td>
        <td class="copy-td down">−${pct(m.max_drawdown_pct)}</td>
        <td class="copy-td">${num(m.sharpe)}</td>
        <td class="copy-td">${num(m.sortino)}</td>
        <td class="copy-td">${pct(m.volatility_pct)}</td>
        <td class="copy-td ${cls(m.total_return_pct)}">${signed(m.total_return_pct)}</td>
      </tr>`;
    }).join("");
    wrap.innerHTML = `
<table class="copy-table">
  <thead><tr>
    <th class="copy-th">STRATEGY</th>
    <th class="copy-th">CAGR</th>
    <th class="copy-th">MAX DD</th>
    <th class="copy-th">SHARPE</th>
    <th class="copy-th">SORTINO</th>
    <th class="copy-th">VOL</th>
    <th class="copy-th">TOTAL</th>
  </tr></thead>
  <tbody>${rows}</tbody>
</table>`;
  }

  function renderChart(strat) {
    const canvas = document.getElementById("mom-chart");
    if (!canvas || !strat.length) return;
    if (momChart) { momChart.destroy(); momChart = null; }
    const css = getComputedStyle(document.documentElement);
    const green = css.getPropertyValue("--green").trim();
    const amber = css.getPropertyValue("--amber").trim();
    const blue = css.getPropertyValue("--blue").trim();
    const inkDim = css.getPropertyValue("--ink-dim").trim();
    const palette = [green, amber, blue, inkDim];
    const dash = [[], [], [], [5, 4]];

    const labels = (strat[0].equity_curve || []).map((p) => p.date);
    momChart = new Chart(canvas, {
      type: "line",
      data: {
        labels,
        datasets: strat.map((s, i) => ({
          label: s.name,
          data: s.equity_curve.map((p) => p.value),
          borderColor: palette[i % palette.length],
          borderWidth: i === 0 ? 2.2 : 1.4,
          borderDash: dash[i % dash.length],
          pointRadius: 0,
          tension: 0.2,
          fill: false,
        })),
      },
      options: {
        animation: false, responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => " " + ctx.dataset.label + ": $" + Number(ctx.raw).toLocaleString(undefined, { maximumFractionDigits: 0 }) } },
        },
        scales: {
          x: { ticks: { color: inkDim, font: { family: "JetBrains Mono", size: 10 }, maxTicksLimit: 8 }, grid: { color: "#1a1a18" } },
          y: {
            type: "logarithmic",
            ticks: { color: inkDim, font: { family: "JetBrains Mono", size: 10 }, callback: (v) => "$" + Number(v).toLocaleString() },
            grid: { color: "#1a1a18" },
          },
        },
      },
    });

    const leg = document.getElementById("mom-legend");
    if (leg) leg.innerHTML = strat.map((s, i) =>
      `<span class="copy-legend"><i class="copy-legend__dot" style="background:${palette[i % palette.length]}"></i>${escapeHtml(s.name)}</span>`).join("");
  }

  // ── Rebalance: preview (no trades) → confirm → execute (paper) ──────────────
  async function previewRebalance() {
    const status = document.getElementById("mom-rebal-status");
    if (status) status.textContent = "COMPUTING ORDERS…";
    try {
      const p = readParams();
      const res = await fetch(`/api/momentum/rebalance/preview?top_n=${p.top_n}&lookback=${p.lookback}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      renderRebal(data, false);
      if (status) status.textContent = "";
    } catch (e) {
      if (status) status.textContent = "ERROR: " + e.message;
    }
  }

  function renderRebal(data, executed) {
    const wrap = document.getElementById("mom-rebal");
    if (!wrap) return;
    const orders = data.orders || [];
    const conn = !!data.connected;
    const rows = orders.length
      ? orders.map((o) => `<tr>
          <td class="copy-td ${o.side === "buy" ? "up" : "down"}">${escapeHtml((o.side || "").toUpperCase())}</td>
          <td class="copy-td copy-td--ticker">${escapeHtml(o.symbol)}</td>
          <td class="copy-td">$${Number(o.notional).toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
          <td class="copy-td">${escapeHtml(o.status || (o.close ? "close" : ""))}</td>
        </tr>`).join("")
      : `<tr><td colspan="4" class="copy-empty">ALREADY ON TARGET — NO TRADES NEEDED</td></tr>`;
    const canExec = conn && orders.length && !executed;
    wrap.innerHTML = `
      <div class="mom-rebal-note ${conn ? "" : "mom-rebal-note--off"}">${escapeHtml(data.note || "")}</div>
      <div class="copy-table-wrap"><table class="copy-table">
        <thead><tr><th class="copy-th">ACTION</th><th class="copy-th">SYMBOL</th><th class="copy-th">AMOUNT</th><th class="copy-th">${executed ? "STATUS" : ""}</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      ${executed
        ? `<div class="mom-rebal-done">✓ ORDERS SUBMITTED TO PAPER · ${new Date().toLocaleString()}</div>`
        : `<button type="button" id="mom-exec-btn" class="run-btn run-btn--sm" ${canExec ? "" : "disabled"}>▸ EXECUTE ON PAPER${conn ? "" : " — CONNECT ALPACA FIRST"}</button>`}`;
    if (canExec) document.getElementById("mom-exec-btn")?.addEventListener("click", executeRebalance);
  }

  async function executeRebalance() {
    if (!window.confirm("Submit these orders to your Alpaca PAPER account?")) return;
    const status = document.getElementById("mom-rebal-status");
    if (status) status.textContent = "SUBMITTING…";
    try {
      const res = await fetch("/api/momentum/rebalance/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(readParams()),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      renderRebal({ ...data, connected: true }, true);
      if (status) status.textContent = "DONE";
    } catch (e) {
      if (status) status.textContent = "ERROR: " + e.message;
    }
  }

  document.getElementById("mom-preview-btn")?.addEventListener("click", previewRebalance);

  // Lazy-load on first tab open (the backtest fetch + ~20y of data takes a moment)
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.tab === "momentum" && !loaded) { loaded = true; runMomentum(); }
    });
  });
  document.getElementById("momentum-form")?.addEventListener("submit", (e) => {
    e.preventDefault();
    runMomentum();
  });
})();
