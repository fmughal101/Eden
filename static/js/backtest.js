// Backtest tab — loads strategy registry, renders param cells dynamically,
// POSTs to /api/backtest, renders results.

let strategiesData = [];

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

function renderBacktestResults(data) {
  document.getElementById("bt-results").style.display = "flex";
  document.getElementById("bt-results").style.flexDirection = "column";
  document.getElementById("bt-results").style.gap = "20px";

  const ret = data.total_return_pct;
  const retEl = document.getElementById("bt-total-return");
  retEl.textContent = (ret >= 0 ? "+" : "") + ret.toFixed(2) + "%";
  retEl.className = "cell__big dot-matrix " + (ret >= 0 ? "up" : "down");

  document.getElementById("bt-final-capital").textContent = fmtDollar(
    data.final_capital,
  );
  document.getElementById("bt-win-rate").textContent =
    data.win_rate_pct.toFixed(1) + "%";
  document.getElementById("bt-num-trades").textContent = String(
    data.num_trades,
  );

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
