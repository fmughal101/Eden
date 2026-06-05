// Tab switching.

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = btn.dataset.tab;
    document
      .querySelectorAll(".tab-btn")
      .forEach((b) => b.classList.toggle("active", b === btn));
    document
      .querySelectorAll(".tab-content")
      .forEach((c) => c.classList.toggle("active", c.id === `tab-${target}`));
  });
});
