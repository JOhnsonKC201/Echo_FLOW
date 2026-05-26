// Echo Flow dashboard — vanilla JS shared helpers.
// Intentionally tiny: no framework, no build step, no CDN dependency.

function $(sel, root) { return (root || document).querySelector(sel); }
function $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

async function fetchJson(url, init) {
  const r = await fetch(url, init);
  if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}`);
  return r.json();
}

// Notifications badge — Phase 9 will replace the placeholder with real polling.
async function refreshBell() {
  const el = document.getElementById("bell-badge");
  if (!el) return;
  try {
    // Endpoint added in Phase 9; tolerate 404 in Phase 0.
    const r = await fetch("/api/notifications/unread.json");
    if (!r.ok) return;
    const { unread } = await r.json();
    if (unread > 0) {
      el.textContent = String(unread);
      el.hidden = false;
    } else {
      el.textContent = "0";
      el.hidden = true;
    }
  } catch { /* swallow; Phase 0 has no endpoint */ }
}

async function toggleTheme() {
  try {
    const r = await fetch("/api/theme", { method: "POST" });
    if (!r.ok) return;
    const { theme } = await r.json();
    if (theme) {
      document.documentElement.setAttribute("data-theme", theme);
      const icon = document.querySelector(".theme-icon");
      if (icon) icon.textContent = theme === "light" ? "☀" : "☾";
    }
  } catch { /* swallow */ }
}

document.addEventListener("DOMContentLoaded", () => {
  refreshBell();
  setInterval(refreshBell, 5000);
  const tb = document.getElementById("theme-toggle");
  if (tb) tb.addEventListener("click", toggleTheme);
});

window.EF = { $, $$, escapeHtml, fetchJson };
