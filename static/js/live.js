// LIVE tab — polls /api/live (Alpaca PAPER account) every 10s and renders the
// real account: portfolio value, today's P&L, positions, and momentum target vs held.

(function () {
  let target = null;       // momentum target {SYMBOL: weight_pct}, fetched once
  let targetAsOf = null;
  let liveEquityChart = null;

  // ── formatting ──────────────────────────────────────────────────────────────
  const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
  const money = (v) => fmtDollar(v);
  const signedMoney = (v) => (v >= 0 ? "+" : "−") + fmtDollar(Math.abs(v));
  const pct = (v) => (v >= 0 ? "+" : "") + Number(v).toFixed(2) + "%";

  function setBig(id, text, cssClass) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    el.className = "cell__big dot-matrix " + (cssClass || "");
  }

  // ── shared header / status bar ───────────────────────────────────────────────
  function setHeader(d) {
    const title = document.getElementById("bot-title");
    if (title) title.textContent = "MOMENTUM · ALPACA PAPER";

    const dot = document.getElementById("status-dot");
    if (dot) dot.className = "dot " + (d.connected ? "running" : "");
    const label = document.getElementById("status-label");
    if (label) label.textContent = d.connected ? "PAPER LIVE" : "NO KEYS";

    const eq = document.getElementById("kpi-equity");
    if (eq) eq.textContent = d.connected ? fmtDollar(d.portfolio_value) : "—";
    const pnl = document.getElementById("kpi-pnl");
    if (pnl) {
      pnl.textContent = d.connected ? pct(d.pl_today_pct) : "—";
      pnl.className = "kpi__value dot-matrix " + (d.connected ? cls(d.pl_today) : "");
    }
    const kp = document.getElementById("kpi-positions");
    if (kp) kp.textContent = d.connected ? String(d.num_positions) : "—";

    // bottom system bar (live cells)
    const sysStrat = document.getElementById("sys-strategy");
    if (sysStrat) sysStrat.textContent = "MOMENTUM TOP-2";
    const sysSig = document.getElementById("sys-signal");
    if (sysSig) {
      sysSig.textContent = d.connected ? "CONNECTED" : "OFFLINE";
      sysSig.className = "sys-cell__v " + (d.connected ? "up" : "down");
    }
  }

  // ── setup card (no keys) ──────────────────────────────────────────────────────
  function showSetup(note) {
    document.getElementById("live-body").style.display = "none";
    const setup = document.getElementById("live-setup");
    setup.style.display = "";
    setup.innerHTML = `
      <div class="copy-setup__icon">⚿</div>
      <div class="copy-setup__title">ALPACA PAPER NOT CONNECTED</div>
      <p class="copy-setup__body">${escapeHtml(note || "Add your Alpaca paper keys to go live.")}</p>
      <ol class="copy-setup__steps">
        <li>Get free paper keys at <span class="copy-setup__link">alpaca.markets</span></li>
        <li>Create a file named <code>.env</code> next to <code>server.py</code></li>
        <li>Add <code>ALPACA_API_KEY=…</code> and <code>ALPACA_SECRET_KEY=…</code></li>
        <li>Restart the server and reload</li>
      </ol>`;
  }

  // ── positions table ───────────────────────────────────────────────────────────
  function renderPositions(positions) {
    const wrap = document.getElementById("live-positions");
    const sub = document.getElementById("live-pos-sub");
    if (!wrap) return;
    if (!positions || !positions.length) {
      wrap.innerHTML = `<div class="no-data">NO OPEN POSITIONS</div>`;
      if (sub) sub.textContent = "0 HELD";
      return;
    }
    if (sub) sub.textContent = `${positions.length} HELD`;
    const rows = positions.map((p) => `
      <tr>
        <td class="copy-td copy-td--ticker">${escapeHtml(p.symbol)}</td>
        <td class="copy-td">${p.qty}</td>
        <td class="copy-td">${fmtDollar(p.avg_entry)}</td>
        <td class="copy-td">${fmtDollar(p.price)}</td>
        <td class="copy-td">${fmtDollar(p.market_value)}</td>
        <td class="copy-td ${cls(p.unrealized_pl)}">${signedMoney(p.unrealized_pl)}</td>
        <td class="copy-td ${cls(p.unrealized_plpc)}">${pct(p.unrealized_plpc)}</td>
        <td class="copy-td ${cls(p.change_today_pct)}">${pct(p.change_today_pct)}</td>
        <td class="copy-td">${p.weight_pct}%</td>
      </tr>`).join("");
    wrap.innerHTML = `
      <table class="copy-table">
        <thead><tr>
          <th class="copy-th">SYMBOL</th>
          <th class="copy-th">QTY</th>
          <th class="copy-th">AVG COST</th>
          <th class="copy-th">PRICE</th>
          <th class="copy-th">MKT VALUE</th>
          <th class="copy-th">UNREAL P&amp;L</th>
          <th class="copy-th">RETURN</th>
          <th class="copy-th">TODAY</th>
          <th class="copy-th">WEIGHT</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  // ── momentum target vs held ───────────────────────────────────────────────────
  function renderTarget(positions) {
    const el = document.getElementById("live-target");
    const sub = document.getElementById("live-target-sub");
    if (!el) return;
    if (!target) { el.innerHTML = `<div class="no-data">TARGET UNAVAILABLE</div>`; return; }
    if (sub && targetAsOf) sub.textContent = `AS OF ${targetAsOf} · TOP-2 MONTHLY`;

    const held = new Set((positions || []).map((p) => p.symbol));
    const targetSyms = Object.keys(target);
    const chips = targetSyms.map((s) => {
      const on = held.has(s);
      return `<span class="live-chip ${on ? "live-chip--on" : "live-chip--off"}">${escapeHtml(s)} ${target[s]}% ${on ? "✓" : "✗"}</span>`;
    }).join("");
    const extra = [...held].filter((s) => !target[s]).map((s) =>
      `<span class="live-chip live-chip--extra">${escapeHtml(s)} ⚠</span>`).join("");
    const aligned = targetSyms.every((s) => held.has(s)) && [...held].every((s) => target[s]);

    el.innerHTML = `
      <div class="live-target__row"><span class="live-target__label">TARGET</span>${chips || "<span class='no-data'>—</span>"}</div>
      ${extra ? `<div class="live-target__row"><span class="live-target__label">EXTRA HELD</span>${extra}</div>` : ""}
      <div class="live-target__note ${aligned ? "up" : "down"}">
        ${aligned ? "✓ Aligned with the momentum target." : "⚠ Drifted from target — rebalance in the MOMENTUM tab."}
      </div>`;
  }

  // ── equity chart (portfolio value over time) ─────────────────────────────────
  function renderEquityChart(curve) {
    const canvas = document.getElementById("live-equity-chart");
    if (!canvas || !curve || curve.length < 2) return;
    if (liveEquityChart) { liveEquityChart.destroy(); liveEquityChart = null; }
    const css = getComputedStyle(document.documentElement);
    const green = css.getPropertyValue("--green").trim();
    const inkDim = css.getPropertyValue("--ink-dim").trim();
    liveEquityChart = new Chart(canvas, {
      type: "line",
      data: {
        labels: curve.map((p) => p.date),
        datasets: [{
          label: "EQUITY", data: curve.map((p) => p.value),
          borderColor: green, borderWidth: 2, pointRadius: 0, tension: 0.25, fill: false,
        }],
      },
      options: {
        animation: false, responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => " $" + Number(ctx.raw).toLocaleString(undefined, { maximumFractionDigits: 2 }) } },
        },
        scales: {
          x: { ticks: { color: inkDim, font: { family: "JetBrains Mono", size: 10 }, maxTicksLimit: 8 }, grid: { color: "#1a1a18" } },
          y: { ticks: { color: inkDim, font: { family: "JetBrains Mono", size: 10 }, callback: (v) => "$" + Number(v).toLocaleString() }, grid: { color: "#1a1a18" } },
        },
      },
    });
  }

  // ── recent trades (filled orders) ────────────────────────────────────────────
  function renderTrades(orders) {
    const wrap = document.getElementById("live-trades");
    const sub = document.getElementById("live-trades-sub");
    if (!wrap) return;
    if (!orders || !orders.length) {
      wrap.innerHTML = `<div class="no-data">NO TRADES YET</div>`;
      if (sub) sub.textContent = "";
      return;
    }
    if (sub) sub.textContent = `${orders.length} FILLED`;
    const rows = orders.map((o) => {
      const buy = o.side === "buy";
      const t = o.filled_at ? new Date(o.filled_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
      return `
      <tr>
        <td class="copy-td">${escapeHtml(t)}</td>
        <td class="copy-td copy-td--ticker">${escapeHtml(o.symbol)}</td>
        <td class="copy-td ${buy ? "up" : "down"}">${buy ? "BUY" : "SELL"}</td>
        <td class="copy-td">${o.qty}</td>
        <td class="copy-td">${fmtDollar(o.price)}</td>
        <td class="copy-td">${fmtDollar(o.value)}</td>
      </tr>`;
    }).join("");
    wrap.innerHTML = `
      <table class="copy-table">
        <thead><tr>
          <th class="copy-th">TIME</th>
          <th class="copy-th">SYMBOL</th>
          <th class="copy-th">SIDE</th>
          <th class="copy-th">QTY</th>
          <th class="copy-th">PRICE</th>
          <th class="copy-th">VALUE</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  async function fetchHistory() {
    try {
      const res = await fetch("/api/live/history");
      const d = await res.json();
      if (d.connected) renderEquityChart(d.curve || []);
    } catch (_) {}
  }

  async function fetchOrders() {
    try {
      const res = await fetch("/api/live/orders?limit=50");
      const d = await res.json();
      if (d.connected) renderTrades(d.orders || []);
    } catch (_) {}
  }

  // ── poll ──────────────────────────────────────────────────────────────────────
  let t0 = 0;
  async function fetchLive() {
    t0 = performance.now();
    try {
      const res = await fetch("/api/live");
      const d = await res.json();
      const lat = document.getElementById("sys-latency");
      if (lat) lat.textContent = Math.max(1, Math.round(performance.now() - t0)) + "ms";

      setHeader(d);
      if (!d.connected) { showSetup(d.note); return; }

      document.getElementById("live-setup").style.display = "none";
      document.getElementById("live-body").style.display = "";
      setBig("live-portfolio-value", money(d.portfolio_value), "");
      setBig("live-pl-today", signedMoney(d.pl_today) + " (" + pct(d.pl_today_pct) + ")", cls(d.pl_today));
      setBig("live-open-pl", signedMoney(d.open_pl), cls(d.open_pl));
      setBig("live-cash", money(d.cash), "");
      renderPositions(d.positions);
      renderTarget(d.positions);

      const lu = document.getElementById("last-updated");
      if (lu && d.updated_at) lu.textContent = "UPDATED " + new Date(d.updated_at).toLocaleTimeString();
    } catch (e) {
      const label = document.getElementById("status-label");
      if (label) label.textContent = "SERVER OFFLINE";
    }
  }

  // The momentum target changes monthly — fetch it once (it's a slower yfinance call).
  async function fetchTarget() {
    try {
      const res = await fetch("/api/momentum/current");
      const d = await res.json();
      target = d.weights || null;
      targetAsOf = d.as_of || null;
    } catch (_) {}
  }

  fetchLive();                    // render the account immediately
  fetchTarget().then(fetchLive);  // re-render once the (slower) target loads
  fetchHistory();                 // equity chart
  fetchOrders();                  // recent trades
  setInterval(fetchLive, 10000);
  setInterval(() => { fetchHistory(); fetchOrders(); }, 60000);
})();
