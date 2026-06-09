// COPY tab — multi-source trade-copying leaderboard + member detail view.
// Sources (Congress STOCK Act, Superinvestor SEC 13F) share one backend contract
// and one UI; a per-source config drives columns, filters, labels and the apiBase.

(function () {
  const tab = document.getElementById("tab-copy");
  if (!tab) return;

  const leaderboardView = document.getElementById("copy-leaderboard");
  const detailView = document.getElementById("copy-detail");
  if (!leaderboardView || !detailView) return;

  // ── Per-source configuration ─────────────────────────────────────────────────
  const SOURCES = {
    congress: {
      label: "CONGRESS",
      apiBase: "/api/congress",
      title: "▸ CONGRESS LEADERBOARD",
      subLive: "STOCK ACT DISCLOSURES · LAST 6 MONTHS",
      unit: "MEMBERS",
      columns: [
        { key: "member", label: "MEMBER" },
        { key: "party", label: "PARTY" },
        { key: "chamber", label: "CHAMBER" },
        { key: "state", label: "STATE" },
        { key: "return_6mo", label: "6MO RETURN" },
        { key: "trade_count", label: "TRADES" },
        { key: "win_rate", label: "WIN RATE" },
      ],
      filters: ["chamber", "party"],
      defaultSort: "trade_count",
      heroReturnLabel: "MEMBER RETURN · 6MO",
      youLabel: "YOU FOLLOWING · 6MO",
      spyLabel: "SPY · 6MO",
      winLabel: "MEMBER WIN RATE",
      chartSub: "EQUAL-WEIGHT · BUY ON DISCLOSURE DATE · vs SPY BUY & HOLD",
      dateLabel: "DATE",
      footer: { k: "CHAMBER", field: "chamber" },
    },
    superinvestors: {
      label: "SUPERINVESTORS",
      apiBase: "/api/superinvestors",
      title: "▸ SUPERINVESTOR LEADERBOARD",
      subLive: "SEC 13F · via DATAROMA · TRAILING ~1Y",
      unit: "MANAGERS",
      columns: [
        { key: "member", label: "MANAGER" },
        { key: "firm", label: "FIRM" },
        { key: "portfolio_value", label: "PORTFOLIO" },
        { key: "return_6mo", label: "RETURN" },
        { key: "trade_count", label: "TRADES" },
        { key: "win_rate", label: "WIN RATE" },
      ],
      filters: [],
      defaultSort: "return_6mo",
      heroReturnLabel: "MANAGER RETURN",
      youLabel: "YOU FOLLOWING",
      spyLabel: "SPY",
      winLabel: "WIN RATE",
      chartSub: "EQUAL-WEIGHT · BUY ON 13F FILING DATE · vs SPY BUY & HOLD",
      dateLabel: "QTR END",
      footer: { k: "FIRM", field: "firm" },
    },
  };

  // ── State ──────────────────────────────────────────────────────────────────
  let activeSource = "congress";
  let allMembers = [];
  let isMock = false;
  let activeSizing = "equal";   // simulation position sizing: "equal" | "amount"
  let activeChamber = "ALL";
  let activeParty = "ALL";
  let activeSort = "trade_count";
  let sortDir = -1; // -1 = desc
  let copyChart = null;

  const cfg = () => SOURCES[activeSource];

  // ── Footer ─────────────────────────────────────────────────────────────────
  function setFooter(firstVal, member, ret) {
    const fv = document.getElementById("cpy-sys-chamber");
    const fk = fv && fv.parentElement ? fv.parentElement.querySelector(".sys-cell__k") : null;
    const fm = document.getElementById("cpy-sys-member");
    const fr = document.getElementById("cpy-sys-return");
    if (fk) fk.textContent = cfg().footer.k;
    if (fv) fv.textContent = firstVal || "—";
    if (fm) fm.textContent = member || "—";
    if (fr) {
      fr.textContent = ret != null ? fmtReturn(ret) : "—";
      fr.className = "sys-cell__v " + (ret > 0 ? "up" : ret < 0 ? "down" : "");
    }
  }

  // ── Helpers ────────────────────────────────────────────────────────────────
  function fmtReturn(pct) {
    if (pct == null) return "—";
    return (pct >= 0 ? "+" : "") + Number(pct).toFixed(1) + "%";
  }

  function fmtAmt(lo, hi) {
    if (!lo && !hi) return "—";
    function fmt(n) {
      if (n >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
      if (n >= 1e3) return "$" + (n / 1e3).toFixed(0) + "k";
      return "$" + n;
    }
    if (lo === hi) return fmt(lo);
    return fmt(lo) + "–" + fmt(hi);
  }

  function fmtMoney(n) {
    if (!n) return "—";
    if (n >= 1e12) return "$" + (n / 1e12).toFixed(2) + "T";
    if (n >= 1e9) return "$" + (n / 1e9).toFixed(1) + "B";
    if (n >= 1e6) return "$" + (n / 1e6).toFixed(0) + "M";
    if (n >= 1e3) return "$" + (n / 1e3).toFixed(0) + "k";
    return "$" + n;
  }

  function partyClass(party) {
    return party === "R" ? "party--rep" : party === "D" ? "party--dem" : "party--ind";
  }

  function returnClass(pct) {
    return pct > 0 ? "up" : pct < 0 ? "down" : "";
  }

  // One leaderboard cell, formatted by column key.
  function cellHtml(col, m) {
    const v = m[col.key];
    switch (col.key) {
      case "member":
        return `<td class="copy-td copy-td--name">${escapeHtml(String(v != null ? v : "—"))}</td>`;
      case "party":
        return `<td class="copy-td"><span class="copy-party ${partyClass(v)}">${escapeHtml(v || "?")}</span></td>`;
      case "return_6mo":
        return `<td class="copy-td ${returnClass(v)}">${fmtReturn(v)}</td>`;
      case "trade_count":
        return `<td class="copy-td dot-matrix">${v != null ? v : 0}</td>`;
      case "win_rate":
        return `<td class="copy-td">${v != null ? v + "%" : "—"}</td>`;
      case "portfolio_value":
        return `<td class="copy-td">${fmtMoney(v)}</td>`;
      default:
        return `<td class="copy-td">${escapeHtml(String(v != null && v !== "" ? v : "—"))}</td>`;
    }
  }

  // ── Source switch (always visible, even on error/loading) ────────────────────
  function sourceSwitchHtml() {
    const btns = Object.keys(SOURCES).map(k =>
      `<button class="copy-filter-btn copy-source-btn ${activeSource === k ? "active" : ""}" data-source="${k}">${SOURCES[k].label}</button>`
    ).join("");
    return `<div class="copy-source-switch">
      <span class="copy-filter-label">SOURCE</span>${btns}
    </div>`;
  }

  function wireSourceSwitch(root) {
    root.querySelectorAll("[data-source]").forEach(btn => {
      btn.addEventListener("click", () => {
        if (activeSource === btn.dataset.source) return;
        activeSource = btn.dataset.source;
        const c = cfg();
        activeSort = c.defaultSort;
        sortDir = -1;
        activeChamber = "ALL";
        activeParty = "ALL";
        allMembers = [];
        loadLeaderboard();
      });
    });
  }

  // ── Leaderboard ────────────────────────────────────────────────────────────
  async function loadLeaderboard() {
    leaderboardView.innerHTML = `
${sourceSwitchHtml()}
<div class="panel">
  <div class="panel__head">
    <span class="panel__title">${cfg().title}</span>
    <span class="panel__sub">${cfg().subLive}</span>
  </div>
  <div class="copy-loading">LOADING ${cfg().unit}…</div>
</div>`;
    wireSourceSwitch(leaderboardView);
    detailView.style.display = "none";
    leaderboardView.style.display = "";
    setFooter("—", "—", null);

    try {
      const res = await fetch(cfg().apiBase + "/members");
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        if (res.status === 503) return renderNeedsKey(err.detail || "");
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      allMembers = data.members || [];
      isMock = !!data.mock;
      renderLeaderboard();
    } catch (err) {
      leaderboardView.innerHTML = `
${sourceSwitchHtml()}
<div class="panel">
  <div class="panel__head"><span class="panel__title">${cfg().title}</span></div>
  <div class="copy-error">ERROR: ${escapeHtml(err.message)}</div>
</div>`;
      wireSourceSwitch(leaderboardView);
    }
  }

  // Friendly 503 card. Congress: FMP key guidance. Superinvestors: Dataroma is a
  // keyless scrape, so a 503 just means the source is unreachable/rate-limited.
  function renderNeedsKey(detail) {
    let body;
    if (activeSource === "superinvestors") {
      body = `
    <div class="copy-setup__icon">◷</div>
    <div class="copy-setup__title">DATA SOURCE NOT RESPONDING</div>
    <p class="copy-setup__body">Superinvestor 13F data is scraped from Dataroma (no key
      required). It's temporarily unreachable — most likely rate-limiting after a burst,
      or a transient network issue.</p>
    <ol class="copy-setup__steps">
      <li>Wait a minute, then reopen this tab</li>
      <li>The last good snapshot is served from disk cache when available</li>
    </ol>`;
    } else {
      const notActive = /401|activate|throttl|limit|retry/i.test(detail || "");
      body = notActive
        ? `
    <div class="copy-setup__icon">◷</div>
    <div class="copy-setup__title">DATA SOURCE NOT RESPONDING</div>
    <p class="copy-setup__body">A key is configured, but Financial Modeling Prep is
      rejecting it right now. A newly created key can take a few minutes to activate
      (and may require confirming your email), and the free tier throttles bursts.</p>
    <ol class="copy-setup__steps">
      <li>Confirm your account email if you just signed up</li>
      <li>Check the key on your FMP dashboard is active</li>
      <li>Wait a couple minutes, then reopen this tab</li>
    </ol>`
        : `
    <div class="copy-setup__icon">⚿</div>
    <div class="copy-setup__title">DATA SOURCE NOT CONFIGURED</div>
    <p class="copy-setup__body">Congress trade data is served via a free Financial
      Modeling Prep API key (no credit card). To enable this tab:</p>
    <ol class="copy-setup__steps">
      <li>Get a free key at <span class="copy-setup__link">site.financialmodelingprep.com</span></li>
      <li>Create a file named <code>.env</code> next to <code>server.py</code></li>
      <li>Add the line <code>FMP_API_KEY=your_key_here</code></li>
      <li>Restart the server and reopen this tab</li>
    </ol>`;
    }
    leaderboardView.innerHTML = `
${sourceSwitchHtml()}
<div class="panel">
  <div class="panel__head">
    <span class="panel__title">${cfg().title}</span>
    <span class="panel__sub">SOURCE UNAVAILABLE</span>
  </div>
  <div class="copy-setup">${body}</div>
</div>`;
    wireSourceSwitch(leaderboardView);
  }

  function renderLeaderboard() {
    const c = cfg();
    let members = allMembers.filter(m => {
      if (c.filters.includes("chamber") && activeChamber !== "ALL" && m.chamber !== activeChamber) return false;
      if (c.filters.includes("party") && activeParty !== "ALL" && m.party !== activeParty) return false;
      return true;
    });

    // sortDir = -1 → descending (largest/best first), +1 → ascending. Nulls always
    // sink to the bottom regardless of direction.
    members.sort((a, b) => {
      const av = a[activeSort], bv = b[activeSort];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (av < bv) return -sortDir;
      if (av > bv) return sortDir;
      return 0;
    });

    const headerCells = c.columns.map(col => {
      const active = activeSort === col.key ? ` copy-th--active` : "";
      const arrow = activeSort === col.key ? (sortDir < 0 ? " ▼" : " ▲") : "";
      return `<th class="copy-th${active}" data-sort="${col.key}">${col.label}${arrow}</th>`;
    }).join("");

    const rows = members.map(m => `
      <tr class="copy-row" data-id="${escapeHtml(m.member_id)}">
        ${c.columns.map(col => cellHtml(col, m)).join("")}
      </tr>`).join("");

    const chamberFilter = c.filters.includes("chamber") ? `
    <div class="copy-filter-group">
      <span class="copy-filter-label">CHAMBER</span>
      <button class="copy-filter-btn ${activeChamber === "ALL" ? "active" : ""}" data-chamber="ALL">ALL</button>
      <button class="copy-filter-btn ${activeChamber === "Senate" ? "active" : ""}" data-chamber="Senate">SENATE</button>
      <button class="copy-filter-btn ${activeChamber === "House" ? "active" : ""}" data-chamber="House">HOUSE</button>
    </div>` : "";
    const partyFilter = c.filters.includes("party") ? `
    <div class="copy-filter-group">
      <span class="copy-filter-label">PARTY</span>
      <button class="copy-filter-btn ${activeParty === "ALL" ? "active" : ""}" data-party="ALL">ALL</button>
      <button class="copy-filter-btn ${activeParty === "D" ? "active" : ""}" data-party="D">DEM</button>
      <button class="copy-filter-btn ${activeParty === "R" ? "active" : ""}" data-party="R">REP</button>
    </div>` : "";

    const colCount = c.columns.length;

    leaderboardView.innerHTML = `
${sourceSwitchHtml()}
<div class="panel">
  <div class="panel__head">
    <span class="panel__title">${c.title}</span>
    <span class="panel__sub">${isMock
      ? '<span class="copy-mock-badge">◆ MOCK DATA</span> · UI PREVIEW'
      : c.subLive}</span>
  </div>
  <div class="copy-controls">
    ${chamberFilter}
    ${partyFilter}
    <span class="copy-count">${members.length} ${c.unit}</span>
  </div>
  <div class="copy-table-wrap">
    <table class="copy-table">
      <thead><tr>${headerCells}</tr></thead>
      <tbody>${rows || `<tr><td colspan="${colCount}" class="copy-empty">NO ${c.unit} FOUND</td></tr>`}</tbody>
    </table>
  </div>
</div>`;

    wireSourceSwitch(leaderboardView);

    // Filter button handlers
    leaderboardView.querySelectorAll("[data-chamber]").forEach(btn => {
      btn.addEventListener("click", () => { activeChamber = btn.dataset.chamber; renderLeaderboard(); });
    });
    leaderboardView.querySelectorAll("[data-party]").forEach(btn => {
      btn.addEventListener("click", () => { activeParty = btn.dataset.party; renderLeaderboard(); });
    });

    // Sort header handlers
    leaderboardView.querySelectorAll(".copy-th").forEach(th => {
      th.addEventListener("click", () => {
        const k = th.dataset.sort;
        if (activeSort === k) sortDir *= -1;
        else { activeSort = k; sortDir = -1; }
        renderLeaderboard();
      });
    });

    // Row click → detail
    leaderboardView.querySelectorAll(".copy-row").forEach(row => {
      row.addEventListener("click", () => loadDetail(row.dataset.id));
    });
  }

  // ── Member detail ──────────────────────────────────────────────────────────
  async function loadDetail(memberId) {
    leaderboardView.style.display = "none";
    detailView.style.display = "";
    detailView.innerHTML = `<div class="copy-loading">LOADING ${activeSource === "superinvestors" ? "MANAGER" : "MEMBER"} DATA…</div>`;

    try {
      const res = await fetch(cfg().apiBase + `/member/${encodeURIComponent(memberId)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      renderDetail(memberId, data.performance, data.trades);
    } catch (err) {
      detailView.innerHTML = `
        <button class="copy-back-btn" id="copy-back">← BACK</button>
        <div class="copy-error">ERROR: ${escapeHtml(err.message)}</div>`;
      document.getElementById("copy-back")?.addEventListener("click", showLeaderboard);
    }
  }

  function renderDetail(memberId, perf, trades) {
    const c = cfg();
    perf = perf || {};
    trades = trades || [];
    const isSI = activeSource === "superinvestors";

    const photoUrl = perf.photo_url || "";

    // Profile meta differs by source: Congress shows party/state/chamber; a fund
    // shows firm / reporting period / portfolio size.
    let metaHtml;
    if (isSI) {
      metaHtml = `
      <span class="copy-firm">${escapeHtml(perf.firm || "—")}</span>
      <span class="copy-profile__sep">·</span>
      <span>${escapeHtml(perf.period || "13F")}</span>
      ${perf.portfolio_value ? `<span class="copy-profile__sep">·</span><span>${fmtMoney(perf.portfolio_value)} PORTFOLIO</span>` : ""}`;
    } else {
      const partyLabel = perf.party === "R" ? "REPUBLICAN" : perf.party === "D" ? "DEMOCRAT" : (perf.party || "INDEPENDENT");
      metaHtml = `
      <span class="copy-party ${partyClass(perf.party)}">${escapeHtml(partyLabel)}</span>
      <span class="copy-profile__sep">·</span>
      <span>${escapeHtml(perf.state || "—")}</span>
      <span class="copy-profile__sep">·</span>
      <span>${escapeHtml(perf.chamber || "—")}</span>`;
    }

    // Hero cells. MEMBER/MANAGER RETURN = how they did (their dates, size-weighted).
    // YOU FOLLOWING + SPY are filled from the simulation so they match the graph.
    const heroVal = (v, cls) => v != null ? `<span class="${cls}">${fmtReturn(v)}</span>` : "—";
    const heroCells = `
      <div class="copy-hero-cell">
        <div class="copy-hero-cell__label">${c.heroReturnLabel}</div>
        <div class="copy-hero-cell__val dot-matrix">${heroVal(perf.return_6mo, returnClass(perf.return_6mo))}</div>
      </div>
      <div class="copy-hero-cell">
        <div class="copy-hero-cell__label">${c.youLabel}</div>
        <div class="copy-hero-cell__val dot-matrix" id="hero-you">…</div>
      </div>
      <div class="copy-hero-cell">
        <div class="copy-hero-cell__label">${c.spyLabel}</div>
        <div class="copy-hero-cell__val dot-matrix" id="hero-spy">…</div>
      </div>
      <div class="copy-hero-cell">
        <div class="copy-hero-cell__label">${c.winLabel}</div>
        <div class="copy-hero-cell__val dot-matrix">${perf.win_rate != null ? perf.win_rate + "%" : "—"}</div>
      </div>`;

    // Breakdown cells — secondary stats.
    const bdCells = [
      ["TOTAL TRADES", perf.total_trades ?? "—"],
      ["PURCHASES",    perf.purchase_count ?? "—"],
      ["SALES",        perf.sale_count ?? "—"],
      ["AVG HOLD",     perf.avg_hold_days != null ? perf.avg_hold_days + "d" : "—"],
      ["MOST BOUGHT",  perf.most_bought_ticker ? `${escapeHtml(perf.most_bought_ticker)} <span class="copy-bd-cell__sub">${perf.most_bought_count}×</span>` : "—"],
      ["MOST SOLD",    perf.most_sold_ticker  ? `${escapeHtml(perf.most_sold_ticker)} <span class="copy-bd-cell__sub">${perf.most_sold_count}×</span>` : "—"],
    ].map(([k, v]) =>
      `<div class="copy-bd-cell">
         <div class="copy-bd-cell__label">${k}</div>
         <div class="copy-bd-cell__val">${v}</div>
       </div>`).join("");

    const sectors = (perf.top_sectors || []);
    const sectorChips = sectors.length
      ? sectors.map(s => `<span class="copy-sector-chip">${escapeHtml(s)}</span>`).join("")
      : `<span class="copy-sector-chip copy-sector-chip--empty">NO SECTOR DATA</span>`;

    // Trade table rows. For superinvestors the TYPE cell shows the 13F activity
    // ("Add 203.99%", "Reduce 0.71%", "Buy", "Sell"); PRICE is the trade-date close.
    const tradeRows = trades.map(t => {
      const typeText = (isSI && t.activity) ? t.activity : t.type;
      return `
      <tr>
        <td class="copy-td">${escapeHtml(t.trade_date || "—")}</td>
        <td class="copy-td copy-td--ticker">${escapeHtml(t.ticker)}</td>
        <td class="copy-td copy-td--name-sm">${escapeHtml(t.asset_name || t.ticker)}</td>
        <td class="copy-td ${t.type === "Purchase" ? "up" : "down"}">${escapeHtml(typeText)}</td>
        <td class="copy-td">${t.price != null ? fmtDollar(t.price) : "—"}</td>
        <td class="copy-td">${fmtAmt(t.amount_low, t.amount_high)}</td>
        <td class="copy-td">${escapeHtml(t.disclosed_date || "—")}</td>
        <td class="copy-td copy-td--lag">${t.disclosure_lag_days != null ? t.disclosure_lag_days + "d" : "—"}</td>
      </tr>`;
    }).join("");

    detailView.innerHTML = `
<button class="copy-back-btn" id="copy-back">← BACK TO LEADERBOARD</button>

<div class="copy-profile">
  <div class="copy-profile__photo-wrap">
    <img class="copy-profile__photo" src="${escapeHtml(photoUrl)}"
         alt="${escapeHtml(perf.member || "")}"
         onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
    <div class="copy-profile__photo-fallback" style="display:${photoUrl ? "none" : "flex"}">${escapeHtml((perf.member || "?")[0])}</div>
  </div>
  <div class="copy-profile__info">
    <div class="copy-profile__name">${escapeHtml((perf.member || "").toUpperCase())}</div>
    <div class="copy-profile__meta">${metaHtml}</div>
  </div>
</div>

<div class="copy-hero">${heroCells}</div>

<div class="panel copy-detail-panel">
  <div class="panel__head">
    <span class="panel__title">▸ TRADE BREAKDOWN</span>
  </div>
  <div class="copy-breakdown">${bdCells}</div>
  <div class="copy-sectors">
    <span class="copy-sectors__label">TOP SECTORS</span>
    <div class="copy-sectors__chips">${sectorChips}</div>
  </div>
</div>

<div class="panel copy-detail-panel">
  <div class="panel__head">
    <span class="panel__title">▸ IF YOU FOLLOWED</span>
    <span class="panel__sub">${c.chartSub}</span>
  </div>
  <div class="copy-chart-controls">
    <span class="copy-legend"><i class="copy-legend__dot copy-legend__dot--port"></i>YOU</span>
    <span class="copy-legend"><i class="copy-legend__dot copy-legend__dot--spy"></i>SPY</span>
    <span class="copy-chart-controls__spacer"></span>
    <span class="copy-simulate__label">SIZING</span>
    <button class="copy-filter-btn copy-size-btn ${activeSizing === "equal" ? "active" : ""}" data-sizing="equal">EQUAL</button>
    <button class="copy-filter-btn copy-size-btn ${activeSizing === "amount" ? "active" : ""}" data-sizing="amount">MIRROR SIZE</button>
    <span class="copy-simulate__label" style="margin-left:10px">CAPITAL $</span>
    <input type="number" id="sim-capital" class="param-cell__input copy-sim-input" value="10000" min="100" step="100" />
    <span id="sim-result" class="copy-sim-result"></span>
  </div>
  <div id="sim-sizing" class="copy-sizing"></div>
  <div class="chart-wrap copy-chart-wrap"><canvas id="copy-chart"></canvas></div>
  <div id="copy-verdict" class="copy-verdict"></div>
  <div id="copy-risk" class="copy-risk"></div>
</div>

<div class="panel copy-detail-panel copy-collapsible">
  <div class="panel__head copy-collapse-head">
    <span class="panel__title"><span class="copy-caret">▾</span> YOUR SIMULATED POSITIONS</span>
    <span class="panel__sub" id="sim-pos-sub"></span>
  </div>
  <div class="copy-table-wrap" id="sim-positions"></div>
</div>

<div class="panel copy-detail-panel copy-collapsible">
  <div class="panel__head copy-collapse-head">
    <span class="panel__title"><span class="copy-caret">▾</span> RECENT TRADES</span>
    <span class="panel__sub">${trades.length} TOTAL</span>
  </div>
  <div class="terminal-table">
    <table class="copy-table">
      <thead>
        <tr>
          <th class="copy-th">${c.dateLabel}</th>
          <th class="copy-th">TICKER</th>
          <th class="copy-th">ASSET</th>
          <th class="copy-th">${isSI ? "ACTIVITY" : "TYPE"}</th>
          <th class="copy-th">PRICE</th>
          <th class="copy-th">AMOUNT</th>
          <th class="copy-th">DISCLOSED</th>
          <th class="copy-th">LAG</th>
        </tr>
      </thead>
      <tbody>${tradeRows || '<tr><td colspan="8" class="copy-empty">NO TRADES</td></tr>'}</tbody>
    </table>
  </div>
</div>`;

    document.getElementById("copy-back")?.addEventListener("click", showLeaderboard);

    // Collapsible panels (positions, recent trades) — toggle body + caret glyph.
    detailView.querySelectorAll(".copy-collapse-head").forEach(head => {
      head.addEventListener("click", () => {
        const panel = head.closest(".copy-collapsible");
        if (!panel) return;
        const collapsed = panel.classList.toggle("collapsed");
        const caret = head.querySelector(".copy-caret");
        if (caret) caret.textContent = collapsed ? "▸" : "▾";
      });
    });

    // Capital recomputes the simulation live (no RUN button — it runs on load).
    const simInput = document.getElementById("sim-capital");
    const runNow = () => runSimulate(memberId, Number(simInput?.value) || 10000);
    simInput?.addEventListener("change", runNow);
    simInput?.addEventListener("keydown", (e) => { if (e.key === "Enter") runNow(); });

    // Sizing toggle (equal vs mirror the member's disclosed size)
    detailView.querySelectorAll(".copy-size-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        if (activeSizing === btn.dataset.sizing) return;
        activeSizing = btn.dataset.sizing;
        detailView.querySelectorAll(".copy-size-btn").forEach(b =>
          b.classList.toggle("active", b.dataset.sizing === activeSizing));
        runNow();
      });
    });

    setFooter(perf[c.footer.field], perf.member, perf.return_6mo);

    // Load equity chart with default $10k
    loadEquityChart(memberId, 10000);
  }

  async function runSimulate(memberId, capital) {
    const resultEl = document.getElementById("sim-result");
    if (resultEl) resultEl.textContent = "CALCULATING…";
    try {
      const data = await fetchSimulate(memberId, capital);
      applySim(data, capital);
    } catch (e) {
      if (resultEl) resultEl.textContent = "ERROR: " + e.message;
    }
  }

  function fetchSimulate(memberId, capital) {
    return fetch(cfg().apiBase + "/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ member_id: memberId, capital, sizing: activeSizing }),
    }).then(r => r.json());
  }

  async function loadEquityChart(memberId, capital) {
    try {
      const data = await fetchSimulate(memberId, capital);
      applySim(data, capital);
    } catch (_) {}
  }

  // Update everything the simulation drives: result line, hero YOU/SPY, sizing
  // note, chart, positions. Hero YOU/SPY read from sim data so they match the graph.
  function applySim(data, capital) {
    const ret = data.total_return_pct;
    const final = data.final_value;
    const resultEl = document.getElementById("sim-result");
    if (resultEl) {
      resultEl.innerHTML = `→ <span class="${returnClass(ret)}">$${Number(final).toLocaleString(undefined, { maximumFractionDigits: 0 })} (${fmtReturn(ret)})</span>`;
    }
    const lbl = document.getElementById("sim-cap-label");
    if (lbl) lbl.textContent = Number(capital).toLocaleString();

    // Hero: YOU (this sim's return) and SPY over the same window (from the curve).
    const youEl = document.getElementById("hero-you");
    if (youEl) youEl.innerHTML = `<span class="${returnClass(ret)}">${fmtReturn(ret)}</span>`;
    const spy = data.spy_curve || [];
    const spyRet = spy.length ? (spy[spy.length - 1].value - capital) / capital * 100 : null;
    const spyEl = document.getElementById("hero-spy");
    if (spyEl && spyRet != null) spyEl.innerHTML = `<span class="${returnClass(spyRet)}">${fmtReturn(spyRet)}</span>`;

    const sizing = document.getElementById("sim-sizing");
    if (sizing) {
      sizing.innerHTML = data.num_buys
        ? (activeSizing === "amount"
            ? `SIZING: <b>MIRROR</b> — each buy weighted by the ${activeSource === "superinvestors" ? "manager's 13F position change" : "member's disclosed dollar size"} · $${Number(capital).toLocaleString()} across ${data.num_buys} buys · no margin`
            : `SIZING: <b>EQUAL</b> — <b>$${Number(data.position_size).toLocaleString(undefined, { maximumFractionDigits: 0 })}</b> per buy · $${Number(capital).toLocaleString()} ÷ ${data.num_buys} buys · no margin`)
        : "";
    }

    // Honest verdict: would copying them actually have beaten just holding SPY, after costs?
    const vs = data.vs_benchmark, m = data.metrics, sm = data.spy_metrics;
    const hasCurve = (data.equity_curve || []).length > 1;
    const vEl = document.getElementById("copy-verdict");
    if (vEl) {
      vEl.innerHTML = (vs && hasCurve)
        ? `<span class="${vs.beats_benchmark ? "up" : "down"}">${vs.beats_benchmark ? "▲ WOULD HAVE BEATEN" : "▼ WOULD HAVE LOST TO"} BUY &amp; HOLD</span>`
          + ` by ${Math.abs(vs.excess_return_pct).toFixed(1)}% · `
          + `${(vs.dd_improvement_pct || 0) > 0 ? "smaller" : "larger"} drawdown · after ${data.cost_bps != null ? data.cost_bps : 5} bps costs`
        : "";
    }
    const rEl = document.getElementById("copy-risk");
    if (rEl) {
      if (m && sm && hasCurve) {
        const row = (label, you, spy, better) =>
          `<div class="copy-risk-row"><span class="copy-risk-k">${label}</span>`
          + `<span class="copy-risk-you ${better ? "up" : ""}">${you}</span>`
          + `<span class="copy-risk-spy">${spy}</span></div>`;
        rEl.innerHTML =
          `<div class="copy-risk-row copy-risk-head"><span class="copy-risk-k"></span><span class="copy-risk-you">YOU</span><span class="copy-risk-spy">SPY</span></div>`
          + row("MAX DRAWDOWN", "−" + m.max_drawdown_pct + "%", "−" + sm.max_drawdown_pct + "%", m.max_drawdown_pct < sm.max_drawdown_pct)
          + row("SHARPE", Number(m.sharpe).toFixed(2), Number(sm.sharpe).toFixed(2), m.sharpe > sm.sharpe)
          + row("SORTINO", Number(m.sortino).toFixed(2), Number(sm.sortino).toFixed(2), m.sortino > sm.sortino);
      } else {
        rEl.innerHTML = "";
      }
    }

    renderEquityChart(data.equity_curve, data.spy_curve, capital);
    renderPositions(data.positions || []);
  }

  function renderPositions(positions) {
    const wrap = document.getElementById("sim-positions");
    const sub = document.getElementById("sim-pos-sub");
    if (!wrap) return;
    if (!positions.length) {
      wrap.innerHTML = `<div class="copy-empty">NO POSITIONS</div>`;
      if (sub) sub.textContent = "";
      return;
    }
    const held = positions.filter(p => p.status === "HELD").length;
    if (sub) sub.textContent = `${positions.length} BUYS · ${held} STILL HELD`;
    const rows = positions.map(p => `
      <tr>
        <td class="copy-td copy-td--ticker">${escapeHtml(p.ticker)}</td>
        <td class="copy-td">${escapeHtml(p.buy_date)}</td>
        <td class="copy-td">${fmtDollar(p.buy_price)}</td>
        <td class="copy-td">$${Number(p.invested).toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
        <td class="copy-td"><span class="copy-pos-status copy-pos-status--${p.status === "HELD" ? "held" : "sold"}">${p.status}</span></td>
        <td class="copy-td">${p.exit_price != null ? fmtDollar(p.exit_price) : "—"}</td>
        <td class="copy-td">${p.current_price != null ? fmtDollar(p.current_price) : "—"}</td>
        <td class="copy-td">$${Number(p.value_now).toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
        <td class="copy-td ${returnClass(p.return_pct)}">${fmtReturn(p.return_pct)}</td>
      </tr>`).join("");
    wrap.innerHTML = `
<table class="copy-table">
  <thead><tr>
    <th class="copy-th">TICKER</th>
    <th class="copy-th">BOUGHT</th>
    <th class="copy-th">BUY PRICE</th>
    <th class="copy-th">INVESTED</th>
    <th class="copy-th">STATUS</th>
    <th class="copy-th">EXIT</th>
    <th class="copy-th">CURRENT</th>
    <th class="copy-th">VALUE NOW</th>
    <th class="copy-th">RETURN</th>
  </tr></thead>
  <tbody>${rows}</tbody>
</table>`;
  }

  function renderEquityChart(curve, spyCurve, capital) {
    const canvas = document.getElementById("copy-chart");
    if (!canvas || !curve || curve.length < 2) return;
    if (copyChart) { copyChart.destroy(); copyChart = null; }

    const green = getComputedStyle(document.documentElement).getPropertyValue("--green").trim();
    const blue = getComputedStyle(document.documentElement).getPropertyValue("--blue").trim();
    const inkDim = getComputedStyle(document.documentElement).getPropertyValue("--ink-dim").trim();

    copyChart = new Chart(canvas, {
      type: "line",
      data: {
        labels: curve.map(p => p.date),
        datasets: [
          {
            label: "PORTFOLIO",
            data: curve.map(p => p.value),
            borderColor: green,
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.3,
            fill: false,
          },
          {
            label: "SPY",
            data: (spyCurve && spyCurve.length === curve.length)
              ? spyCurve.map(p => p.value)
              : curve.map(() => capital),
            borderColor: blue,
            borderWidth: 1.5,
            borderDash: [5, 4],
            pointRadius: 0,
            tension: 0.3,
            fill: false,
          },
        ],
      },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: ctx => " $" + Number(ctx.raw).toLocaleString(undefined, { maximumFractionDigits: 0 }),
            },
          },
        },
        scales: {
          x: { ticks: { color: inkDim, font: { family: "JetBrains Mono", size: 10 } }, grid: { color: "#1a1a18" } },
          y: {
            ticks: {
              color: inkDim,
              font: { family: "JetBrains Mono", size: 10 },
              callback: v => "$" + Number(v).toLocaleString(),
            },
            grid: { color: "#1a1a18" },
          },
        },
      },
    });
  }

  // ── Navigation ─────────────────────────────────────────────────────────────
  function showLeaderboard() {
    if (copyChart) { copyChart.destroy(); copyChart = null; }
    detailView.style.display = "none";
    leaderboardView.style.display = "";
    setFooter("—", "—", null);
  }

  // ── Init: load when tab becomes active ────────────────────────────────────
  let loaded = false;
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      if (btn.dataset.tab === "copy" && !loaded) {
        loaded = true;
        loadLeaderboard();
      }
    });
  });
})();
