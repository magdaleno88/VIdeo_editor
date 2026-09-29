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
  if (message && !window.confirm(message)) {
    event.preventDefault();
    return;
  }
  const button = event.target.querySelector("button[type='submit']");
  if (button) {
    button.disabled = true;
    button.textContent = "Processing…";
  }
});

document.getElementById("source-upload")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const file = form.elements.source.files[0];
  const status = document.getElementById("source-upload-status");
  if (!file) return;
  status.textContent = "Uploading and validating with ffprobe…";
  try {
    const response = await fetch("/sources/upload", {
      method: "POST",
      headers: {"x-filename": file.name, "x-title": form.elements.title.value},
      body: file,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Upload failed");
    window.location.assign(`/dashboard/sources/${payload.id}?notice=Source uploaded`);
  } catch (error) {
    status.textContent = error.message;
  }
});

document.getElementById("concept-clips")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const rows = [...form.querySelectorAll("tbody tr")];
  const clips = rows.map((row) => ({
    output_order: Number(row.querySelector("[name=order]").value),
    role: row.querySelector("[name=role]").value,
    source_start: Number(row.querySelector("[name=start]").value),
    source_end: Number(row.querySelector("[name=end]").value),
    target_output_duration: Number(row.querySelector("[name=duration]").value),
    speed_recommendation: 1.0,
    moment_id: null,
  }));
  const status = document.getElementById("clip-save-status");
  try {
    const response = await fetch(`/concepts/${form.dataset.concept}/clips`, {
      method: "PATCH",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({clips}),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Save failed");
    status.textContent = "Clip plan saved.";
    window.location.reload();
  } catch (error) {
    status.textContent = error.message;
  }
});
