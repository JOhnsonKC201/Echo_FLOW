"""PyWebView launcher — opens the dashboard as a native desktop window.

Runs as a separate process so:
  - the daemon stays headless under run_silent.vbs
  - closing the window doesn't kill the daemon
  - a window crash can't take down dictation

Usage:
    python -m src.dashboard.window               # uses data/dashboard.port
    python -m src.dashboard.window --port 8766   # explicit
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path


_STATE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "dashboard_window.json"


def _icon_path() -> str | None:
    """Best-effort path to the window/taskbar icon. Prefers the multi-size
    .ico (crisp at every taskbar scale); falls back to the .png. Resolves
    against the PyInstaller bundle when frozen, else the repo root."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent.parent.parent
    for rel in ("assets/icon.ico", "assets/icon.png", "src/dashboard/static/logo.png"):
        p = base / rel
        if p.exists():
            return str(p)
    return None


_SPLASH_HTML = """<!doctype html>
<html><head><meta charset='utf-8'><title>Echo Flow</title>
<style>
  html,body{margin:0;height:100%;background:#0d1014;color:#e7e9ee;
    font:500 13px/1.4 'Inter','Segoe UI',system-ui,sans-serif;
    -webkit-font-smoothing:antialiased;overflow:hidden;}
  .wrap{position:absolute;inset:0;display:flex;flex-direction:column;
    align-items:center;justify-content:center;gap:18px;}
  .logo{font:600 28px/1 'JetBrains Mono',ui-monospace,Consolas,monospace;
    color:#3eaf6f;letter-spacing:-0.02em;}
  .name{font-size:14px;font-weight:600;letter-spacing:0.02em;}
  .dots{display:flex;gap:6px;margin-top:4px;}
  .dots span{width:6px;height:6px;border-radius:50%;background:#3eaf6f;
    opacity:.25;animation:p 1.2s infinite ease-in-out;}
  .dots span:nth-child(2){animation-delay:.15s}
  .dots span:nth-child(3){animation-delay:.3s}
  @keyframes p{0%,80%,100%{opacity:.25;transform:scale(.85)}
                40%{opacity:1;transform:scale(1)}}
</style></head><body><div class='wrap'>
  <div class='logo'>&#9646;&#9646;&#9647;</div>
  <div class='name'>Echo Flow</div>
  <div class='dots'><span></span><span></span><span></span></div>
</div></body></html>"""


def _primary_screen_size() -> tuple[int, int]:
    """Best-effort primary monitor size (Windows-only). Falls back to 1920x1080."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    except Exception:
        return 1920, 1080


def _load_window_state() -> dict:
    """Return persisted {width, height, x, y} (best-effort)."""
    try:
        if _STATE_FILE.exists():
            data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_window_state(window) -> None:
    """Snapshot the current window size. Called on close.

    Only width/height are persisted — restoring an absolute x/y from a
    previous session breaks badly when the user unplugs an external
    monitor (window opens off-screen with no way to recover).

    Uses PyWebView's live get_size() when available; the static .width
    /.height attributes are only the *initial* values passed at create
    time and never change to reflect user resizing.
    """
    try:
        w, h = None, None
        try:
            size = window.get_size()
            if size:
                w, h = int(size[0]), int(size[1])
        except Exception:
            pass
        if w is None:
            w = int(getattr(window, "width", 0)) or 1280
        if h is None:
            h = int(getattr(window, "height", 0)) or 820
        state = {"width": w, "height": h}
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def _wait_for_server(url: str, timeout_s: float = 8.0) -> bool:
    """Poll /api/healthz until 200 or timeout. The daemon may still be warming."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def _resolve_port(explicit: int | None) -> int:
    if explicit:
        return explicit
    from . import read_port_file
    p = read_port_file()
    if p:
        return p
    return 8766  # last-resort default


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="echoflow-dashboard")
    parser.add_argument("--port", type=int, default=None,
                        help="Override port (default: read data/dashboard.port)")
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--no-window", action="store_true",
                        help="Fall back to opening in default browser")
    args = parser.parse_args(argv)

    port = _resolve_port(args.port)
    url = f"http://{args.host}:{port}/"
    health = f"http://{args.host}:{port}/api/healthz"

    if not _wait_for_server(health):
        print(f"echoflow-dashboard: server at {url} not responding. "
              f"Is the Echo Flow daemon running?", file=sys.stderr)
        return 2

    if args.no_window:
        import webbrowser
        webbrowser.open(url)
        return 0

    try:
        import webview  # type: ignore
    except ImportError:
        # PyWebView not installed (e.g. non-Windows or skipped) — fall back.
        import webbrowser
        webbrowser.open(url)
        return 0

    # Windows taskbar: give the process its own AppUserModelID so it groups
    # under the Echo Flow icon instead of the generic python.exe pin, and the
    # taskbar uses our icon rather than the interpreter's.
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("EchoFlow.Dashboard")
    except Exception:
        pass

    try:
        state = _load_window_state()
        width = int(state.get("width", 1280))
        height = int(state.get("height", 820))
        # Center on the primary monitor each launch — restoring a saved x/y
        # strands the window off-screen when an external monitor unplugs.
        sw, sh = _primary_screen_size()
        x = max(0, (sw - width) // 2)
        y = max(0, (sh - height) // 2)
        # Inline splash paints on the first frame so there is no white flash
        # while the dashboard's heavier templates render.
        # Open maximized so the dashboard fills the screen on launch; the saved
        # width/height become the "restore down" size. `maximized` is honored by
        # current PyWebView — older builds raise TypeError, so we fall back to
        # an explicit win.maximize() once the backend is up (see below).
        create_kwargs = dict(
            html=_SPLASH_HTML,
            width=width, height=height, x=x, y=y,
            min_size=(900, 600),
            background_color="#0d1014",
            text_select=True,
        )
        try:
            win = webview.create_window("Echo Flow", maximized=True, **create_kwargs)
        except TypeError:
            win = webview.create_window("Echo Flow", **create_kwargs)
        try:
            win.events.closing += lambda: _save_window_state(win)
        except Exception:
            pass
        # Swap from splash → real dashboard once webview is up, and ensure the
        # window is maximized even on PyWebView builds that ignored the kwarg.
        def _swap_to_dashboard():
            try:
                win.maximize()
            except Exception:
                pass
            try:
                win.load_url(url)
            except Exception:
                pass
        # Pass our icon so the window + taskbar show the Echo Flow mark even when
        # running from source (where the default would be the python.exe icon).
        # `icon` is accepted by current PyWebView; tolerate older builds.
        icon = _icon_path()
        try:
            webview.start(func=_swap_to_dashboard, icon=icon)
        except TypeError:
            webview.start(func=_swap_to_dashboard)
    except Exception as e:
        # WebView2 runtime missing or similar — graceful fallback.
        print(f"echoflow-dashboard: PyWebView failed ({e}); opening in browser.",
              file=sys.stderr)
        import webbrowser
        webbrowser.open(url)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
