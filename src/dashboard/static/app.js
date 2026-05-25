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
    const r = await fetch("/api/notifications/unread_count");
    if (!r.ok) return;
    const { count } = await r.json();
    if (count > 0) {
      el.textContent = String(count);
      el.hidden = false;
    } else {
      el.hidden = true;
    }
  } catch { /* swallow; Phase 0 has no endpoint */ }
}

document.addEventListener("DOMContentLoaded", () => {
  refreshBell();
  setInterval(refreshBell, 5000);
});

window.EF = { $, $$, escapeHtml, fetchJson };
