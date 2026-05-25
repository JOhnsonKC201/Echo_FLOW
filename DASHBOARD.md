# Echo Flow Desktop Dashboard

A native desktop window for managing Echo Flow — history, insights, custom
vocabulary, snippets, style profiles, transforms, scratchpads, settings, and
notifications. Inspired by Wispr Flow's IA, built entirely local-first.

## Design principles

- **Computer-first.** Desktop user is the trusted primary. The dashboard
  binds to `127.0.0.1` only — no auth, because the loopback boundary is
  the auth model. Anyone on this machine can already read `config.yaml`
  and inject keystrokes; no extra protection helps.
- **Never blocks dictation.** The Flask server runs in a daemon thread.
  The PyWebView window runs in a *separate* process. Crashes in either
  cannot wedge the hotkey path.
- **Works offline forever.** No CDN, no telemetry, no SPA framework,
  no Node toolchain. Server-rendered HTML + tiny vanilla JS.

## How it runs

| Surface | Process | Lifetime |
|---|---|---|
| Flask server | Daemon thread inside `src.main.App` | Lives with the daemon |
| PyWebView window | Separate `python -m src.dashboard.window` process | Lives until you close it |

The daemon picks a free port starting at `dashboard.port` (default `8766`)
and scans up to four ports forward if it's busy. The chosen port is written
to `data/dashboard.port` so the tray launcher and `run_dashboard.bat`
always find the right one.

## Opening the dashboard

Three ways:

1. **Tray → Open Dashboard** (recommended). Spawns the window subprocess.
2. **`run_dashboard.bat`** — same effect; useful for a Start Menu shortcut.
3. **Browser fallback** — if PyWebView or WebView2 are missing,
   `webbrowser.open("http://127.0.0.1:<port>/")`.

## Configuration

```yaml
dashboard:
  enabled: true
  host: "127.0.0.1"          # never bind to 0.0.0.0
  port: 8766                 # auto-fallback 8767..8770
  theme: "dark"              # dark | light
  open_window_on_start: false
```

## Requirements

- `flask>=3.0` (already a dep for the mobile bridge)
- `jinja2>=3` (Flask's template engine — usually pulled in transitively)
- `pywebview>=5.0` (Windows only) — needs WebView2 runtime, which ships
  with Windows 11. On Windows 10 install from
  <https://developer.microsoft.com/microsoft-edge/webview2/>.

If PyWebView or WebView2 are missing, the launcher gracefully falls back
to opening the dashboard in your default browser.

## Adding a Start Menu shortcut

Right-click `run_dashboard.bat` → Send to → Desktop (create shortcut).
Move the shortcut to `%APPDATA%\Microsoft\Windows\Start Menu\Programs\`
and rename it to "Echo Flow Dashboard". Optionally change its icon to
the Echo Flow icon.

## Security

- Bound to `127.0.0.1` — local-machine attackers only.
- `Host:` header is checked on every request; anything not in
  `{127.0.0.1, localhost}:{port..port+4}` returns HTTP 400. Cheap defense
  against DNS-rebinding from a malicious webpage.
- No cookies, no sessions, no CSRF tokens in v1 (loopback + single user).
- Werkzeug dev server is fine here for the same reason the mobile bridge
  uses it: single-user, local-only.

## Phased rollout

Phase 0 (this commit): shell only — sidebar nav + 9 placeholder sections.
Subsequent phases fill them in one at a time. See
`C:\Users\johns\.claude\plans\image-3-image-4-playful-valley.md` for the
full roadmap.
