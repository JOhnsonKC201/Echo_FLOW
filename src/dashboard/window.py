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

    try:
        state = _load_window_state()
        # We deliberately do NOT restore x/y — see _save_window_state docstring.
        kwargs = dict(
            width=int(state.get("width", 1280)),
            height=int(state.get("height", 820)),
            min_size=(900, 600),
            text_select=True,
        )
        win = webview.create_window("Echo Flow", url, **kwargs)
        # Persist size/position on close (best-effort).
        try:
            win.events.closing += lambda: _save_window_state(win)
        except Exception:
            pass
        webview.start()
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
