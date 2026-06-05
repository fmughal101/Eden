// Scrolling ticker tape. Renders DOM once, fetches real quotes from
// /api/quotes, and updates percentages in place so the animation never
// resets. Scroll distance is measured in pixels (Web Animations API)
// so the seamless loop works regardless of CSS gap/padding.

(function () {
  const SYMBOLS = [
    "SPY",  "QQQ",  "AAPL", "MSFT", "NVDA", "TSLA",
    "AMZN", "GOOG", "META", "AMD",  "BRK.B","JPM",
    "XLE",  "XLF",  "GLD",  "TLT",  "BTC",  "ETH",
  ];
  const REFRESH_MS = 60_000;
  const SCROLL_DURATION_MS = 60_000;

  const track = document.getElementById("ticker-track");
  if (!track) return;

  // ── 1. Build markup once, two passes so the loop is seamless.
  // tabular-nums keeps digit widths stable as percentages update.
  const html = [];
  for (let pass = 0; pass < 2; pass++) {
    for (const sym of SYMBOLS) {
      html.push(`
        <span class="ticker__item" data-sym="${sym}" data-pass="${pass}">
          <span class="ticker__sym">${sym}</span>
          <span class="ticker__pct" style="font-variant-numeric: tabular-nums;">—</span>
        </span>
        <span class="ticker__sep">·</span>
      `);
    }
  }
  track.innerHTML = html.join("");

  // ── 2. Update in place — never re-render, never restart the animation.
  function applyQuotes(quotes) {
    const bySymbol = new Map(quotes.map(q => [q.symbol, q]));
    for (const item of track.querySelectorAll(".ticker__item")) {
      const q = bySymbol.get(item.dataset.sym);
      const pctEl = item.querySelector(".ticker__pct");
      if (!q) continue;
      if (Number.isFinite(q.pct)) {
        const sign = q.pct >= 0 ? "+" : "";
        pctEl.textContent = `${sign}${q.pct.toFixed(2)}%`;
        pctEl.classList.toggle("up", q.pct >= 0);
        pctEl.classList.toggle("down", q.pct < 0);
      } else if (Number.isFinite(q.price)) {
        pctEl.textContent = `$${q.price.toFixed(2)}`;
        pctEl.classList.remove("up", "down");
      }
    }
  }

  // ── 3. Measure the exact pixel offset of the first second-copy item.
  // That offset is what -50% *should* equal but doesn't when flex gap
  // is in play (half-gap fencepost). Using the measured value makes
  // the loop seamless regardless of CSS spacing choices.
  let scrollAnim = null;
  function startScroll() {
    const anchor = track.querySelector('[data-pass="1"]');
    if (!anchor) return;
    const shift =
      anchor.getBoundingClientRect().left -
      track.getBoundingClientRect().left;
    if (shift <= 0) return;

    track.style.animation = "none"; // override the CSS animation
    if (scrollAnim) scrollAnim.cancel();
    scrollAnim = track.animate(
      [
        { transform: "translateX(0)" },
        { transform: `translateX(-${shift}px)` },
      ],
      { duration: SCROLL_DURATION_MS, iterations: Infinity, easing: "linear" }
    );
  }

  async function refresh() {
    try {
      const res = await fetch("/api/quotes", { cache: "no-store" });
      if (!res.ok) return;
      const data = await res.json();
      if (Array.isArray(data.quotes)) applyQuotes(data.quotes);
    } catch {
      /* keep prior values on transient failure */
    }
  }

  // Wait for fonts so widths are stable before measuring.
  const ready = document.fonts ? document.fonts.ready : Promise.resolve();
  ready.then(refresh).then(startScroll);

  setInterval(refresh, REFRESH_MS);
})();
