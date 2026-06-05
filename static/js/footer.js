// System bar — keeps the footer's data-tab in sync with the active tab so
// the CSS can show only that tab's cells. Per-tab field values (strategy,
// latency, period, etc.) are written directly by each tab's own JS into
// the cells' IDs.

(function () {
  const bar = document.querySelector(".system-bar");
  if (!bar) return;

  function setActiveTab(tab) {
    if (tab) bar.dataset.tab = tab;
  }

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => setActiveTab(btn.dataset.tab));
  });

  // Initialise from whichever tab button is already .active at load time.
  const active = document.querySelector(".tab-btn.active");
  if (active) setActiveTab(active.dataset.tab);
})();
