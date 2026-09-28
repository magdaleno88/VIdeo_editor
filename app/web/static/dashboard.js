document.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-seek]");
  if (!trigger) return;
  const requested = document.getElementById(trigger.dataset.target || "");
  const player = requested || document.querySelector("video");
  if (!player) return;
  player.currentTime = Number.parseFloat(trigger.dataset.seek) || 0;
  player.scrollIntoView({ behavior: "smooth", block: "center" });
  player.play().catch(() => {});
});

document.addEventListener("submit", (event) => {
  const message = event.target.dataset.confirm;
  if (message && !window.confirm(message)) event.preventDefault();
});
