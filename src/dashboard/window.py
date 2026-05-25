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
import sys
import time
import urllib.request
from pathlib import Path


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
        webview.create_window(
            "Echo Flow",
            url,
            width=1280,
            height=820,
            min_size=(900, 600),
            text_select=True,
        )
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
