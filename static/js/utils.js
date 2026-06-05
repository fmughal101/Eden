// Shared helpers used across all tabs.
// Globals declared here (charts) are accessible from later-loaded scripts
// because classic <script> tags share a single global lexical environment.

const charts = {};

function fmtDollar(n, opts = {}) {
  if (n == null) return "—";
  const { compact = false } = opts;
  return (
    "$" +
    Number(n).toLocaleString("en-US", {
      minimumFractionDigits: compact ? 0 : 2,
      maximumFractionDigits: compact ? 0 : 2,
    })
  );
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function indicatorsFor(data) {
  if (Array.isArray(data.indicators) && data.indicators.length) {
    return data.indicators;
  }
  return [
    { key: "sma_short", label: `SMA${data.short_window || ""}`, color: "#5fd17a", dash: true },
    { key: "sma_long",  label: `SMA${data.long_window  || ""}`, color: "#d4a13a", dash: true },
  ];
}

// Indicators without an explicit `pane` default to "price" so legacy strategies
// (sma_crossover) keep their existing behavior.
function priceIndicators(data)       { return indicatorsFor(data).filter(i => (i.pane || "price") === "price"); }
function oscillatorIndicators(data)  { return indicatorsFor(data).filter(i =>  i.pane === "oscillator"); }

function buildLegend(legendEl, indicators) {
  if (!legendEl) return;
  const indicatorItems = indicators
    .map((ind) => {
      return `<div class="legend-item"><div class="legend-line" style="background: ${ind.color}"></div>${ind.label}</div>`;
    })
    .join("");
  legendEl.innerHTML = `
    <div class="legend-item"><div class="legend-line" style="background: #e8e6df"></div>Price</div>
    ${indicatorItems}
    <div class="legend-item"><div class="legend-dot" style="background: #5fd17a"></div>Buy</div>
    <div class="legend-item"><div class="legend-dot" style="background: #e04a2f"></div>Sell</div>
  `;
}

// VOID-styled Chart.js: off-black bg, hairline grid, off-white price line,
// dashed indicators, triangle buy/sell markers.
function buildChart(canvasId, data) {
  const history = data.price_history || [];
  if (!history.length) return;

  const labels = history.map((h) => h.date);
  const prices = history.map((h) => h.close);
  const buyPoints = history.map((h) => (h.signal === 1 ? h.close : null));
  const sellPoints = history.map((h) => (h.signal === -1 ? h.close : null));
  // Only price-scale indicators on the main chart; oscillator-scale ones
  // (RSI/MACD/ATR) render on a sibling canvas via buildOscillatorChart.
  const indicators = priceIndicators(data);

  const datasets = [
    {
      label: "Price",
      data: prices,
      borderColor: "#e8e6df",
      borderWidth: 1.25,
      pointRadius: 0,
      tension: 0,
      fill: false,
    },
  ];

  for (const ind of indicators) {
    datasets.push({
      label: ind.label,
      data: history.map((h) => h[ind.key]),
      borderColor: ind.color,
      borderWidth: 1,
      borderDash: ind.dash ? [3, 3] : [],
      pointRadius: 0,
      tension: 0,
      fill: false,
    });
  }

  datasets.push(
    {
      label: "Buy",
      data: buyPoints,
      borderColor: "#5fd17a",
      backgroundColor: "#5fd17a",
      pointRadius: 6,
      pointStyle: "triangle",
      showLine: false,
      type: "scatter",
    },
    {
      label: "Sell",
      data: sellPoints,
      borderColor: "#e04a2f",
      backgroundColor: "#e04a2f",
      pointRadius: 6,
      pointStyle: "triangle",
      pointRotation: 180,
      showLine: false,
      type: "scatter",
    },
  );

  const cfg = {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          ticks: {
            maxTicksLimit: 8,
            color: "#8a8880",
            font: { size: 10, family: "JetBrains Mono" },
          },
          grid: { color: "rgba(255,255,255,0.04)", drawTicks: false },
          border: { color: "#2a2a28" },
        },
        y: {
          ticks: {
            color: "#8a8880",
            font: { size: 10, family: "JetBrains Mono" },
            callback: (v) => "$" + Math.round(v),
          },
          grid: { color: "rgba(255,255,255,0.04)", drawTicks: false },
          border: { color: "#2a2a28" },
        },
      },
    },
  };

  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart(document.getElementById(canvasId), cfg);
}

// Off-scale indicators (RSI 0-100, MACD around 0, ATR positive) on a sibling
// canvas. The wrapper element gets `--empty` when there's nothing to plot so
// the surrounding card hides the slot via CSS. `wrapId` is the container that
// gets the show/hide class; `canvasId` is the canvas inside it.
function buildOscillatorChart(canvasId, wrapId, data) {
  const wrap = document.getElementById(wrapId);
  const canvas = document.getElementById(canvasId);
  if (!wrap || !canvas) return;

  const indicators = oscillatorIndicators(data);
  if (!indicators.length) {
    wrap.classList.add("chart-wrap--empty");
    if (charts[canvasId]) {
      charts[canvasId].destroy();
      delete charts[canvasId];
    }
    return;
  }
  wrap.classList.remove("chart-wrap--empty");

  const history = data.price_history || [];
  const labels = history.map((h) => h.date);

  const datasets = indicators.map((ind) => ({
    label: ind.label,
    data: history.map((h) => h[ind.key]),
    borderColor: ind.color,
    borderWidth: 1,
    borderDash: ind.dash ? [3, 3] : [],
    pointRadius: 0,
    tension: 0,
    fill: false,
  }));

  const cfg = {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          ticks: {
            maxTicksLimit: 8,
            color: "#8a8880",
            font: { size: 10, family: "JetBrains Mono" },
          },
          grid: { color: "rgba(255,255,255,0.04)", drawTicks: false },
          border: { color: "#2a2a28" },
        },
        y: {
          ticks: {
            color: "#8a8880",
            font: { size: 10, family: "JetBrains Mono" },
          },
          grid: { color: "rgba(255,255,255,0.04)", drawTicks: false },
          border: { color: "#2a2a28" },
        },
      },
    },
  };

  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart(canvas, cfg);
}

function renderTrades(elId, trades, limit = 8) {
  const el = document.getElementById(elId);
  if (!el) return;
  if (!trades || !trades.length) {
    el.innerHTML = '<div class="no-data">NO TRADES YET</div>';
    return;
  }

  const rows = [...trades]
    .reverse()
    .slice(0, limit)
    .map((t) => {
      const action = String(t.action || "").toUpperCase();
      const pnlCell =
        t.pnl != null
          ? `<span class="trade-pnl ${t.pnl >= 0 ? "pos" : "neg"}">${t.pnl >= 0 ? "+" : "-"}$${Math.abs(t.pnl).toFixed(0)}</span>`
          : `<span class="trade-muted">—</span>`;
      return `
<div class="trade-row">
  <span><span class="badge ${escapeHtml(action)}">${escapeHtml(action)}</span></span>
  <span class="trade-price">${fmtDollar(t.price)}</span>
  <span class="trade-num">${t.shares}</span>
  <span>${pnlCell}</span>
  <span class="trade-date">${escapeHtml(t.date || "")}</span>
</div>`;
    })
    .join("");

  el.innerHTML = `
<div class="trade-table">
  <div class="trade-header">
    <span>ACTION</span>
    <span>PRICE</span>
    <span>SHARES</span>
    <span>P/L</span>
    <span>DATE</span>
  </div>
  ${rows}
</div>`;
}
