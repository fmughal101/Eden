// Top-right clock + bottom runtime counter.

(function () {
  const startedAt = Date.now();
  const pad = (n) => String(n).padStart(2, "0");

  function tick() {
    const now = new Date();
    const clock = document.getElementById("clock");
    if (clock) {
      clock.textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
    }

    const runtime = document.getElementById("sys-runtime");
    if (runtime) {
      const s = Math.floor((Date.now() - startedAt) / 1000);
      const h = Math.floor(s / 3600);
      const m = Math.floor((s % 3600) / 60);
      const sec = s % 60;
      runtime.textContent = `${pad(h)}:${pad(m)}:${pad(sec)}`;
    }
  }

  tick();
  setInterval(tick, 1000);
})();
