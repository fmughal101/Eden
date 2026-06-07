// Research tab — free lite version (yfinance only, no AI)

(function () {
  const form = document.getElementById("research-form");
  const input = document.getElementById("research-symbol");
  const submitBtn = document.getElementById("research-submit");
  const status = document.getElementById("research-status");
  const resultEl = document.getElementById("research-result");
  if (!form || !resultEl) return;

  // ── Footer cells ───────────────────────────────────────────────────────────
  function setFooter(ticker, price) {
    const t = document.getElementById("rsh-sys-ticker");
    const p = document.getElementById("rsh-sys-price");
    if (t) t.textContent = ticker || "—";
    if (p) p.textContent = price != null ? fmtDollar(price) : "—";
  }

  // ── Formatting helpers ─────────────────────────────────────────────────────
  const FUND_FORMAT = {
    marketCap:        (v) => fmtCompactDollar(v),
    trailingPE:       (v) => Number(v).toFixed(2),
    forwardPE:        (v) => Number(v).toFixed(2),
    profitMargins:    (v) => fmtPct(v),
    returnOnEquity:   (v) => fmtPct(v),
    debtToEquity:     (v) => Number(v).toFixed(2),
    revenueGrowth:    (v) => fmtPct(v),
    dividendYield:    (v) => fmtPct(v),
    beta:             (v) => Number(v).toFixed(2),
    fiftyTwoWeekHigh: (v) => fmtDollar(v),
    fiftyTwoWeekLow:  (v) => fmtDollar(v),
  };
  const FUND_LABEL = {
    marketCap: "MARKET CAP", trailingPE: "P/E (TTM)", forwardPE: "P/E (FWD)",
    profitMargins: "MARGIN", returnOnEquity: "ROE", debtToEquity: "D/E",
    revenueGrowth: "REV GROWTH", dividendYield: "DIV YIELD", beta: "BETA",
    fiftyTwoWeekHigh: "52W HIGH", fiftyTwoWeekLow: "52W LOW",
    sector: "SECTOR", industry: "INDUSTRY",
  };

  function fmtCompactDollar(v) {
    if (v == null) return "—";
    const n = Number(v);
    if (n >= 1e12) return "$" + (n / 1e12).toFixed(2) + "T";
    if (n >= 1e9)  return "$" + (n / 1e9 ).toFixed(2) + "B";
    if (n >= 1e6)  return "$" + (n / 1e6 ).toFixed(2) + "M";
    return "$" + n.toLocaleString();
  }
  function fmtPct(v) {
    if (v == null) return "—";
    return (Number(v) * 100).toFixed(2) + "%";
  }
  function fmtDate(str) {
    if (!str) return "—";
    // ISO timestamp → YYYY-MM-DD, unix timestamp → date string
    if (/^\d+$/.test(str)) {
      return new Date(Number(str) * 1000).toISOString().slice(0, 10);
    }
    return String(str).slice(0, 10);
  }

  // ── Sparkline (canvas) ─────────────────────────────────────────────────────
  let sparkChart = null;
  function renderSparkline(priceHistory) {
    const canvas = document.getElementById("rsh-sparkline");
    if (!canvas || !priceHistory || priceHistory.length < 2) return;
    if (sparkChart) { sparkChart.destroy(); sparkChart = null; }
    const labels = priceHistory.map(p => p.date);
    const values = priceHistory.map(p => p.close);
    const first = values[0];
    const last = values[values.length - 1];
    const color = last >= first ? getComputedStyle(document.documentElement).getPropertyValue("--green").trim()
                               : getComputedStyle(document.documentElement).getPropertyValue("--red").trim();
    sparkChart = new Chart(canvas, {
      type: "line",
      data: {
        labels,
        datasets: [{
          data: values,
          borderColor: color,
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.3,
          fill: false,
        }],
      },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: {
          x: { display: false },
          y: { display: false },
        },
      },
    });
  }

  // ── Card rendering ─────────────────────────────────────────────────────────
  function renderCard(data) {
    const f = data.fundamentals || {};
    const news = data.news || [];
    const lastClose = f.fiftyTwoWeekHigh != null ? null : null; // pulled from price_history
    const priceHistory = data.price_history || [];
    const lastPrice = priceHistory.length ? priceHistory[priceHistory.length - 1].close : null;

    const fundRows = Object.entries(f)
      .filter(([k]) => k !== "longBusinessSummary")
      .map(([k, v]) => {
        const label = FUND_LABEL[k] || k.toUpperCase();
        const fmt = FUND_FORMAT[k];
        const value = fmt ? fmt(v) : escapeHtml(String(v));
        return `<div class="rsh-fund-row"><span class="rsh-fund-row__k">${label}</span><span class="rsh-fund-row__v">${value}</span></div>`;
      }).join("");

    const newsRows = news.map(n => `
      <div class="rsh-news-item">
        <div class="rsh-news-item__meta">${escapeHtml(fmtDate(n.published))} · ${escapeHtml(n.publisher || "—")}</div>
        <a class="rsh-news-item__title" href="${escapeHtml(n.link || "#")}" target="_blank" rel="noopener">${escapeHtml(n.title)}</a>
      </div>`).join("");

    const businessSummary = f.longBusinessSummary
      ? `<p class="rsh-card__about">${escapeHtml(f.longBusinessSummary)}</p>`
      : "";

    resultEl.innerHTML = `
<div class="rsh-card">
  <div class="rsh-card__head">
    <div class="rsh-card__title">
      <span class="rsh-card__sym">${escapeHtml(data.symbol)}</span>
      <span class="rsh-card__sector">${escapeHtml((f.sector || "—").toUpperCase())} · ${escapeHtml((f.industry || "—").toUpperCase())}</span>
    </div>
    ${lastPrice != null ? `<div class="rsh-card__price dot-matrix">${fmtDollar(lastPrice)}</div>` : ""}
  </div>

  <div class="rsh-sparkline-wrap">
    <canvas id="rsh-sparkline"></canvas>
  </div>

  <div class="rsh-fundamentals">
    <div class="rsh-fundamentals__head">▸ FUNDAMENTALS</div>
    <div class="rsh-fund-grid">${fundRows}</div>
    ${businessSummary}
  </div>

  ${newsRows ? `<div class="rsh-news"><div class="rsh-fundamentals__head">▸ RECENT NEWS</div>${newsRows}</div>` : ""}

  <div class="rsh-card__foot">
    ${data.cached ? "CACHED" : "FRESH"} · FETCHED ${escapeHtml((data.fetched_at || "").slice(0, 19).replace("T", " "))} UTC
  </div>
</div>`;

    renderSparkline(priceHistory);
    setFooter(data.symbol, lastPrice);
  }

  function renderError(msg) {
    resultEl.innerHTML = `<div class="rsh-error">${escapeHtml(msg)}</div>`;
  }

  // ── Submit handler ─────────────────────────────────────────────────────────
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const symbol = (input.value || "").trim().toUpperCase();
    if (!symbol) return;
    submitBtn.disabled = true;
    submitBtn.textContent = "▸ LOADING…";
    if (status) status.textContent = "Fetching fundamentals and news…";
    resultEl.innerHTML = "";

    try {
      const res = await fetch("/api/research", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      renderCard(data);
      if (status) status.textContent = data.cached ? "DONE · cache hit" : "DONE";
    } catch (err) {
      renderError(`ERROR: ${err.message}`);
      setFooter(symbol, null);
      if (status) status.textContent = "FAILED";
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "▸ RESEARCH";
    }
  });
})();
