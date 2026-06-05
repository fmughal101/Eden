// Research tab — submits a ticker, renders the resulting card.

(function () {
  const form = document.getElementById("research-form");
  const input = document.getElementById("research-symbol");
  const submitBtn = document.getElementById("research-submit");
  const status = document.getElementById("research-status");
  const resultEl = document.getElementById("research-result");
  if (!form || !resultEl) return;

  // ── Footer cells (research-tab) ────────────────────────────────────────────
  function setFooter(ticker, rating) {
    const t = document.getElementById("rsh-sys-ticker");
    const r = document.getElementById("rsh-sys-rating");
    if (t) t.textContent = ticker || "—";
    if (r) {
      r.textContent = rating || "—";
      r.className =
        "sys-cell__v " +
        (rating === "BUY" ? "up" : rating === "SELL" ? "down" : "");
    }
  }

  // ── Card rendering ─────────────────────────────────────────────────────────
  // Fundamentals come from yfinance — pretty-print large numbers, percentages
  // for ratios, and pass through strings.
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

  function renderCard(data) {
    const t = data.thesis || {};
    const f = data.fundamentals || {};
    const sources = data.sources || [];
    const rating = (t.rating || "HOLD").toUpperCase();
    const conf = Number.isFinite(t.confidence) ? t.confidence : 0;

    const fundRows = Object.entries(f)
      .filter(([k]) => k !== "longBusinessSummary")
      .map(([k, v]) => {
        const label = FUND_LABEL[k] || k.toUpperCase();
        const fmt = FUND_FORMAT[k];
        const value = fmt ? fmt(v) : escapeHtml(String(v));
        return `<div class="rsh-fund-row"><span class="rsh-fund-row__k">${label}</span><span class="rsh-fund-row__v">${value}</span></div>`;
      }).join("");

    const bull = (t.bull || []).map(b => `<li>${escapeHtml(b)}</li>`).join("");
    const bear = (t.bear || []).map(b => `<li>${escapeHtml(b)}</li>`).join("");

    const srcRows = sources.map(s =>
      `<li><a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.title || s.url)}</a></li>`
    ).join("");

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
    <div class="rsh-card__rating">
      <span class="rating-pill rating-pill--${rating.toLowerCase()}">${rating}</span>
      <div class="confidence">
        <span class="confidence__label">CONFIDENCE</span>
        <div class="confidence__bar"><div class="confidence__fill" style="width:${conf}%"></div></div>
        <span class="confidence__num">${conf}%</span>
      </div>
    </div>
  </div>

  <p class="rsh-card__summary">${escapeHtml(t.summary || "")}</p>

  <div class="rsh-card__grid">
    <div class="rsh-thesis rsh-thesis--bull">
      <div class="rsh-thesis__head">▲ BULL CASE</div>
      <ul>${bull || "<li class=\"rsh-empty\">—</li>"}</ul>
    </div>
    <div class="rsh-thesis rsh-thesis--bear">
      <div class="rsh-thesis__head">▼ BEAR CASE</div>
      <ul>${bear || "<li class=\"rsh-empty\">—</li>"}</ul>
    </div>
  </div>

  <div class="rsh-fundamentals">
    <div class="rsh-fundamentals__head">▸ FUNDAMENTALS</div>
    <div class="rsh-fund-grid">${fundRows}</div>
    ${businessSummary}
  </div>

  ${srcRows ? `<div class="rsh-sources"><div class="rsh-sources__head">▸ SOURCES</div><ol>${srcRows}</ol></div>` : ""}

  <div class="rsh-card__foot">
    NOT FINANCIAL ADVICE · ${data.cached ? "CACHED" : "FRESH"} · FETCHED ${escapeHtml(data.fetched_at || "")}
  </div>
</div>`;
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
    submitBtn.textContent = "▸ THINKING…";
    if (status) status.textContent = "Fetching fundamentals · running web search · generating thesis…";
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
      setFooter(data.symbol, data.thesis?.rating);
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
