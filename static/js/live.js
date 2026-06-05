// Live tab — polls /api/state every 10s and renders bot state.

function renderParams(data) {
  const rows = [
    ["Symbol", data.symbol],
    ["Short SMA", (data.short_window || "—") + " D"],
    ["Long SMA",  (data.long_window  || "—") + " D"],
    ["Position size", (data.position_size_pct ?? "—") + "%"],
    ["Stop loss",     (data.stop_loss_pct     ?? "—") + "%"],
    ["SMA (short)", fmtDollar(data.sma_short)],
    ["SMA (long)",  fmtDollar(data.sma_long)],
  ];
  const el = document.getElementById("params");
  if (!el) return;
  el.innerHTML = rows
    .map(
      ([k, v]) =>
        `<div class="param-row"><span class="param-name">${k}</span><span class="param-value">${escapeHtml(String(v))}</span></div>`,
    )
    .join("");
}

function updateDashboard(data) {
  const titleEl = document.getElementById("bot-title");
  if (titleEl) titleEl.textContent = `SMA CROSSOVER · ${data.symbol || "—"}`;

  const dot = document.getElementById("status-dot");
  const label = document.getElementById("status-label");
  if (dot)   dot.className   = "dot " + (data.status === "running" ? "running" : "");
  if (label) label.textContent = (data.status || "offline").toUpperCase();

  const ret =
    ((data.portfolio_value - data.initial_capital) / data.initial_capital) * 100;

  // Live KPIs (per-tab)
  const pv = document.getElementById("portfolio-val");
  if (pv) pv.textContent = fmtDollar(data.portfolio_value);

  const retEl = document.getElementById("total-return");
  if (retEl) {
    retEl.textContent = (ret >= 0 ? "+" : "") + ret.toFixed(2) + "%";
    retEl.className = "cell__big dot-matrix " + (ret >= 0 ? "up" : "down");
  }

  const cp = document.getElementById("current-price");
  if (cp) cp.textContent = data.current_price ? fmtDollar(data.current_price) : "—";

  const sh = document.getElementById("shares-held");
  if (sh) sh.textContent = data.shares_held != null ? String(data.shares_held) : "—";

  // Top status-bar KPIs
  const eq = document.getElementById("kpi-equity");
  if (eq) eq.textContent = fmtDollar(data.portfolio_value, { compact: true });

  const pnl = document.getElementById("kpi-pnl");
  if (pnl) {
    pnl.textContent = (ret >= 0 ? "+" : "") + ret.toFixed(2) + "%";
    pnl.className = "kpi__value dot-matrix " + (ret >= 0 ? "up" : "down");
  }

  const trades = data.trades || [];
  const kt = document.getElementById("kpi-trades");
  if (kt) kt.textContent = String(trades.length);

  const closed = trades.filter((t) => t.pnl != null);
  const wins   = closed.filter((t) => t.pnl >= 0).length;
  const wr = closed.length ? (wins / closed.length) * 100 : null;
  const kw = document.getElementById("kpi-winrate");
  if (kw) kw.textContent = wr != null ? wr.toFixed(0) + "%" : "—";

  // Header signal pill
  const sigEl = document.getElementById("signal-indicator");
  if (sigEl) {
    if (data.signal === 1) {
      sigEl.className = "signal-pill signal-buy";
      sigEl.textContent = "↑ GOLDEN CROSS";
    } else if (data.signal === -1) {
      sigEl.className = "signal-pill signal-sell";
      sigEl.textContent = "↓ DEATH CROSS";
    } else {
      sigEl.className = "signal-pill signal-hold";
      sigEl.textContent = "— HOLD";
    }
  }

  if (data.last_updated) {
    const d = new Date(data.last_updated);
    const lu = document.getElementById("last-updated");
    if (lu) lu.textContent = "UPDATED " + d.toLocaleTimeString();
  }

  // Bottom system bar (live cells)
  const sysStrat = document.getElementById("sys-strategy");
  if (sysStrat) sysStrat.textContent = `SMA · ${data.symbol || "—"}`;

  const sysSig = document.getElementById("sys-signal");
  if (sysSig) {
    sysSig.textContent =
      data.signal === 1 ? "BUY" : data.signal === -1 ? "SELL" : "HOLD";
    sysSig.className =
      "sys-cell__v " +
      (data.signal === 1 ? "up" : data.signal === -1 ? "down" : "");
  }

  buildLegend(document.getElementById("live-legend"), indicatorsFor(data));
  buildChart("priceChart", data);
  buildOscillatorChart("liveOscChart", "live-osc-wrap", data);
  renderTrades("trade-log", data.trades);
  renderParams(data);
}

let lastFetchStart = 0;
async function fetchAndUpdate() {
  lastFetchStart = performance.now();
  try {
    const res = await fetch("/api/state");
    if (!res.ok) throw new Error("API error");
    const data = await res.json();
    const latency = Math.max(1, Math.round(performance.now() - lastFetchStart));
    const lat = document.getElementById("sys-latency");
    if (lat) lat.textContent = latency + "ms";
    updateDashboard(data);
  } catch (e) {
    const sl = document.getElementById("status-label");
    if (sl) sl.textContent = "SERVER OFFLINE";
  }
}

fetchAndUpdate();
setInterval(fetchAndUpdate, 10000);
