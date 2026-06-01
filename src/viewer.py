"""HTML history viewer: dumps SQLite into a self-contained HTML file
and opens it in the default browser. Static HTML — no server, no dependencies."""
from __future__ import annotations

import datetime as dt
import html
import json
import sqlite3
import webbrowser
from pathlib import Path


HTML_TMPL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Echo Flow — History</title>
<style>
  :root {
    --bg: #0f1115;
    --panel: #1a1d24;
    --border: #2a2f3a;
    --muted: #888;
    --accent: #58c77a;
    --warn: #e6a23c;
    --raw: #2a1f1f;
    --clean: #1f2a23;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: #e7e9ee; font-family: ui-sans-serif, system-ui, sans-serif; }
  header { padding: 18px 24px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  header h1 { margin: 0; font-size: 18px; font-weight: 600; }
  header .stat { color: var(--muted); font-size: 13px; }
  #search { flex: 1; min-width: 220px; padding: 9px 12px; border-radius: 8px; background: var(--panel); color: #fff; border: 1px solid var(--border); font-size: 14px; }
  main { padding: 16px 24px; max-width: 1200px; margin: 0 auto; }
  .row { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
  .meta { color: var(--muted); font-size: 12px; margin-bottom: 8px; display: flex; gap: 14px; flex-wrap: wrap; }
  .meta .tag { background: #252a35; padding: 2px 8px; border-radius: 999px; font-size: 11px; }
  .pair { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .raw, .clean { padding: 10px 12px; border-radius: 8px; font-size: 14px; line-height: 1.5; white-space: pre-wrap; }
  .raw { background: var(--raw); border-left: 3px solid #c66; }
  .clean { background: var(--clean); border-left: 3px solid var(--accent); }
  .label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--muted); margin-bottom: 4px; }
  .nores { color: var(--muted); padding: 40px; text-align: center; }
  @media (max-width: 700px) { .pair { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<header>
  <h1>🎙 Echo Flow</h1>
  <span class="stat">__STATS__</span>
  <input id="search" placeholder="Search dictations…" autofocus />
</header>
<main id="rows">__ROWS__</main>
<script>
  const data = __DATA__;
  const rowsEl = document.getElementById('rows');
  const search = document.getElementById('search');
  function render(filter) {
    const q = filter.trim().toLowerCase();
    const filtered = q ? data.filter(r =>
      (r.raw||'').toLowerCase().includes(q) ||
      (r.clean||'').toLowerCase().includes(q) ||
      (r.window||'').toLowerCase().includes(q)
    ) : data;
    if (!filtered.length) { rowsEl.innerHTML = '<div class="nores">No matches.</div>'; return; }
    rowsEl.innerHTML = filtered.map(r => `
      <div class="row">
        <div class="meta">
          <span>${escapeHtml(String(r.ts))}</span>
          <span class="tag">${escapeHtml(r.lang)}</span>
          <span class="tag">${escapeHtml(r.style)}</span>
          <span class="tag">${escapeHtml(String(r.dur))}ms</span>
          ${r.window ? `<span class="tag" title="window">${escapeHtml(r.window)}</span>` : ''}
        </div>
        <div class="pair">
          <div>
            <div class="label">Raw (Whisper)</div>
            <div class="raw">${escapeHtml(r.raw)}</div>
          </div>
          <div>
            <div class="label">Cleaned</div>
            <div class="clean">${escapeHtml(r.clean)}</div>
          </div>
        </div>
      </div>
    `).join('');
  }
  function escapeHtml(s) {
    return (s||'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
  search.addEventListener('input', e => render(e.target.value));
  render('');
</script>
</body>
</html>
"""


def render_history(db_path: str, out_path: str | None = None, open_browser: bool = True) -> str:
    """Render SQLite history → HTML file. Returns the file path."""
    if not Path(db_path).exists():
        rows = []
    else:
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.execute(
                "SELECT ts, window_title, style, language, duration_ms, raw_text, cleaned_text "
                "FROM dictations ORDER BY ts DESC LIMIT 1000"
            )
            rows = cur.fetchall()
        except Exception:
            rows = []

    data = []
    for ts, win, style, lang, dur, raw, cleaned in rows:
        data.append({
            "ts": dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
            "window": (win or "")[:60],
            "style": style or "default",
            "lang": lang or "en",
            "dur": dur or 0,
            "raw": raw or "",
            "clean": cleaned or "",
        })

    stats = f"{len(data)} dictations"
    if data:
        total_ms = sum(r["dur"] for r in data)
        stats += f" • {total_ms/1000:.0f}s of speech"
    rows_html = "" if data else '<div class="nores">No dictations yet. Speak something first!</div>'

    out = (HTML_TMPL
        .replace("__STATS__", html.escape(stats))
        .replace("__ROWS__", rows_html)
        .replace("__DATA__", json.dumps(data, ensure_ascii=False)))

    if out_path is None:
        out_path = str(Path(db_path).parent / "history.html")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out)
    if open_browser:
        webbrowser.open(f"file:///{Path(out_path).resolve().as_posix()}")
    return out_path
