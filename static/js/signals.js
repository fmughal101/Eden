// Signals tab — polls /api/signals every 5s.

function fmtSignalTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  const diffSec = (Date.now() - d.getTime()) / 1000;
  if (diffSec < 60)    return `${Math.max(0, Math.floor(diffSec))}S AGO`;
  if (diffSec < 3600)  return `${Math.floor(diffSec / 60)}M AGO`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}H AGO`;
  return d.toLocaleDateString() + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function renderSignals(data) {
  const stats = data.stats || { total: 0, by_status: {} };
  const by = stats.by_status || {};
  const set = (id, v) => {
    const el = document.getElementById(id);
    if (el) el.textContent = v;
  };
  set("sig-total",    stats.total || 0);
  set("sig-received", by.received || 0);
  set("sig-executed", by.executed || 0);
  set("sig-rejected", by.rejected || 0);

  const signals = data.signals || [];
  const el = document.getElementById("signal-log");
  if (!el) return;

  if (!signals.length) {
    el.innerHTML = '<div class="no-data">NO SIGNALS RECEIVED YET</div>';
    return;
  }

  const rows = signals
    .map((s) => {
      const actionText = s.action ? String(s.action).toUpperCase() : null;
      const actionCell = actionText
        ? `<span class="badge ${escapeHtml(actionText)}">${escapeHtml(actionText)}</span>`
        : '<span class="trade-muted">—</span>';
      const price = s.price != null ? fmtDollar(s.price) : '<span class="trade-muted">—</span>';
      const symbol = s.symbol ? escapeHtml(s.symbol) : '<span class="trade-muted">—</span>';
      const strategy = s.strategy ? escapeHtml(s.strategy) : '<span class="trade-muted">—</span>';
      const statusClass = `status-${escapeHtml(s.status || "unknown")}`;
      const notes = s.notes || "";

      return `
<div class="sig-row">
  <span class="trade-date" title="${escapeHtml(s.received_at || "")}">${fmtSignalTime(s.received_at)}</span>
  <span>${actionCell}</span>
  <span class="trade-num">${symbol}</span>
  <span class="trade-price">${price}</span>
  <span class="trade-muted">${strategy}</span>
  <span><span class="sig-status ${statusClass}">${escapeHtml(s.status || "—")}</span></span>
  <span class="sig-notes" title="${escapeHtml(notes)}">${escapeHtml(notes) || "—"}</span>
</div>`;
    })
    .join("");

  el.innerHTML = `
<div class="sig-table">
  <div class="sig-header">
    <span>WHEN</span>
    <span>ACTION</span>
    <span>SYMBOL</span>
    <span>PRICE</span>
    <span>STRATEGY</span>
    <span>STATUS</span>
    <span>NOTES</span>
  </div>
  ${rows}
</div>`;

  const upd = document.getElementById("sig-updated");
  if (upd) upd.textContent = "REFRESHED " + new Date().toLocaleTimeString();

  // Footer (signals tab)
  const todayEl = document.getElementById("sig-sys-today");
  if (todayEl) {
    const startOfDay = new Date();
    startOfDay.setHours(0, 0, 0, 0);
    const todayCount = signals.filter(
      (s) => s.received_at && new Date(s.received_at) >= startOfDay,
    ).length;
    todayEl.textContent = String(todayCount);
  }

  const lastEl = document.getElementById("sig-sys-last");
  if (lastEl) lastEl.textContent = signals[0] ? fmtSignalTime(signals[0].received_at) : "—";
}

async function fetchSignals() {
  try {
    const res = await fetch("/api/signals?limit=100");
    if (!res.ok) return;
    const data = await res.json();
    renderSignals(data);
  } catch (e) {
    /* noop */
  }
}

fetchSignals();
setInterval(fetchSignals, 5000);
