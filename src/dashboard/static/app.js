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

function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

async function toggleTheme() {
  try {
    const r = await fetch("/api/theme", { method: "POST" });
    if (!r.ok) return;
    const { theme } = await r.json();
    if (!theme) return;
    const apply = () => {
      document.documentElement.setAttribute("data-theme", theme);
      const icon = document.querySelector(".theme-icon");
      if (icon) icon.textContent = theme === "light" ? "☀" : "☾";
    };
    // Soft full-page cross-fade where supported; instant otherwise.
    if (document.startViewTransition && !prefersReducedMotion()) {
      document.startViewTransition(apply);
    } else {
      apply();
    }
  } catch { /* swallow */ }
}

// Off-canvas sidebar drawer for narrow viewports. Desktop never opens it
// (the hamburger is display:none), so this is inert there.
function setupNavDrawer() {
  const toggle = document.getElementById("nav-toggle");
  const backdrop = document.getElementById("sidebar-backdrop");
  const sidebar = document.getElementById("sidebar");
  if (!toggle || !sidebar) return;

  const setOpen = (open) => {
    document.body.classList.toggle("nav-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  };
  const close = () => setOpen(false);

  toggle.addEventListener("click", () =>
    setOpen(!document.body.classList.contains("nav-open")));
  if (backdrop) backdrop.addEventListener("click", close);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && document.body.classList.contains("nav-open")) close();
  });
  // Tapping a destination closes the drawer so the target page is visible.
  sidebar.querySelectorAll(".nav-item").forEach(a => a.addEventListener("click", close));
  // Returning to desktop width should never leave a stuck-open drawer.
  window.matchMedia("(min-width: 861px)").addEventListener("change", (e) => {
    if (e.matches) close();
  });
}

// Command palette (Cmd/Ctrl+K) — keyboard-first navigation. Entries are
// derived from the sidebar nav so the palette never drifts from the menu.
function setupCommandPalette() {
  const root = document.getElementById("cmdk");
  const input = document.getElementById("cmdk-input");
  const list = document.getElementById("cmdk-list");
  if (!root || !input || !list) return;

  const entries = $$("#sidebar .nav-item").map(a => ({
    label: (a.textContent || "").trim(),
    url: a.getAttribute("href"),
  })).filter(e => e.label && e.url);
  if (!entries.length) return;

  let selected = 0;
  let lastFocus = null;

  const render = (q) => {
    const needle = q.trim().toLowerCase();
    const matches = needle
      ? entries.filter(e => e.label.toLowerCase().includes(needle))
      : entries;
    selected = 0;
    if (!matches.length) {
      list.innerHTML = '<li class="cmdk-empty">No pages match.</li>';
      return [];
    }
    list.innerHTML = "";
    matches.forEach((e, i) => {
      const li = document.createElement("li");
      li.className = "cmdk-item";
      li.id = "cmdk-opt-" + i;
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", i === 0 ? "true" : "false");
      li.dataset.url = e.url;
      li.textContent = e.label;
      li.addEventListener("click", () => { window.location.href = e.url; });
      li.addEventListener("mousemove", () => setSelected(i));
      list.appendChild(li);
    });
    input.setAttribute("aria-activedescendant", "cmdk-opt-0");
    return matches;
  };

  const setSelected = (i) => {
    const items = $$(".cmdk-item", list);
    if (!items.length) return;
    selected = (i + items.length) % items.length;
    items.forEach((el, idx) => el.setAttribute("aria-selected", idx === selected ? "true" : "false"));
    const el = items[selected];
    input.setAttribute("aria-activedescendant", el.id);
    el.scrollIntoView({ block: "nearest" });
  };

  const open = () => {
    if (!root.hidden) return;
    lastFocus = document.activeElement;
    input.value = "";
    render("");
    root.hidden = false;
    input.focus();
  };
  const close = () => {
    if (root.hidden) return;
    root.hidden = true;
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  };

  // Global open shortcut: Cmd/Ctrl+K.
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      root.hidden ? open() : close();
    }
  });

  input.addEventListener("input", () => render(input.value));
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSelected(selected + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSelected(selected - 1); }
    else if (e.key === "Enter") {
      e.preventDefault();
      const el = $$(".cmdk-item", list)[selected];
      if (el && el.dataset.url) window.location.href = el.dataset.url;
    } else if (e.key === "Escape") { e.preventDefault(); close(); }
  });
  root.addEventListener("click", (e) => {
    if (e.target.hasAttribute("data-cmdk-close")) close();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  refreshBell();
  setInterval(refreshBell, 5000);
  const tb = document.getElementById("theme-toggle");
  if (tb) tb.addEventListener("click", toggleTheme);
  setupNavDrawer();
  setupCommandPalette();
});

window.EF = { $, $$, escapeHtml, fetchJson };

/* ====================================================================== *
 * Premium motion layer — count-up stats, fill-from-zero meters, and a
 * sweeping WPM gauge. The CSS entrance (app.css) handles block reveals;
 * this handles the *values inside* them.
 *
 * Flash-free by construction: cards start at opacity 0 (CSS entrance), so
 * the one-frame "real value" never shows — by the time a card fades in,
 * its numbers already read 0 and its bars are empty, then they animate up.
 * Triggered on scroll-into-view so below-the-fold content animates too.
 * Fully skipped under prefers-reduced-motion (values stay at final state).
 * ====================================================================== */
(function premiumMotion() {
  const root = document.documentElement;
  if (!root.classList.contains("ef-js")) return;     // no gate → no JS path
  if (prefersReducedMotion()) return;                // respect the user

  const NUM_SEL   = ".wf-hero-num, .stat-value, .tile-value, .wf-microstat .n";
  const BAR_SEL   = ".wf-app-bar > span, .wf-split-bar > span, " +
                    ".wf-rank-meter > span, .wf-row .sparkbar > span, .usage-bar";
  const GAUGE_SEL = ".wf-gauge-fill";

  // Group commas back in, preserving a fixed number of decimals.
  function fmt(value, decimals, comma) {
    const s = value.toFixed(decimals);
    if (!comma) return s;
    const [intPart, frac] = s.split(".");
    const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return frac != null ? grouped + "." + frac : grouped;
  }

  // --- pre-pass: capture targets and zero everything out, synchronously. ---
  const numEls = $$(NUM_SEL).filter(el => /\d/.test(el.textContent));
  numEls.forEach(el => {
    const raw = el.textContent.trim();
    // prefix (e.g. "") · number (commas/decimals) · suffix (e.g. "%")
    const m = raw.match(/^(\D*)(-?[\d,]*\.?\d+)(.*)$/s);
    if (!m) return;
    el.dataset.efFinal    = raw;
    el.dataset.efPrefix   = m[1];
    el.dataset.efSuffix   = m[3];
    el.dataset.efTarget   = m[2].replace(/,/g, "");
    el.dataset.efDecimals = String((m[2].split(".")[1] || "").length);
    el.dataset.efComma    = m[2].includes(",") ? "1" : "0";
    el.textContent = m[1] +
      fmt(0, +el.dataset.efDecimals, el.dataset.efComma === "1") + m[3];
  });

  const barEls = $$(BAR_SEL).filter(el => el.style.width);
  barEls.forEach(el => { el.dataset.efW = el.style.width; el.style.width = "0%"; });

  const gaugeEls = $$(GAUGE_SEL).filter(el => el.getAttribute("stroke-dasharray"));
  gaugeEls.forEach(el => {
    el.dataset.efOff = el.getAttribute("stroke-dashoffset");
    el.setAttribute("stroke-dashoffset", el.getAttribute("stroke-dasharray"));
  });

  // --- activation: run the animation for one element, once. ---
  const done = new WeakSet();
  function countUp(el) {
    const target = parseFloat(el.dataset.efTarget);
    const dec = +el.dataset.efDecimals;
    const comma = el.dataset.efComma === "1";
    const { efPrefix: pre, efSuffix: suf, efFinal: fin } = el.dataset;
    if (!isFinite(target) || target === 0) { el.textContent = fin; return; }
    const dur = 900;
    let start = null;
    function step(ts) {
      if (start === null) start = ts;
      const p = Math.min((ts - start) / dur, 1);
      const eased = 1 - Math.pow(1 - p, 3);            // easeOutCubic
      el.textContent = pre + fmt(target * eased, dec, comma) + suf;
      if (p < 1) requestAnimationFrame(step);
      else el.textContent = fin;                       // exact server format
    }
    requestAnimationFrame(step);
  }
  function activate(el) {
    if (done.has(el)) return;
    done.add(el);
    if (el.dataset.efFinal !== undefined) countUp(el);
    else if (el.dataset.efW !== undefined) el.style.width = el.dataset.efW;
    else if (el.dataset.efOff !== undefined) el.setAttribute("stroke-dashoffset", el.dataset.efOff);
  }

  const targets = numEls.concat(barEls, gaugeEls);
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach(e => {
        if (e.isIntersecting) { activate(e.target); io.unobserve(e.target); }
      });
    }, { threshold: 0.25, rootMargin: "0px 0px -6% 0px" });
    targets.forEach(t => io.observe(t));
  } else {
    targets.forEach(activate);
  }
})();
