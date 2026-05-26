"""Echo Flow — dev convenience launcher for the dashboard window.

The production entry point is the daemon (`python -m src.main`), which owns
the tray icon, global hotkey, voice pipeline, AND the Flask dashboard server.
The dashboard window is opened on-demand from the daemon's tray (or via the
configurable global hotkey, default Ctrl+Win).

This file remains for one developer workflow only: when the daemon is already
running, you can re-launch *just* the dashboard window without going through
the tray. It is a thin shell over `src.dashboard.window`.

Use:  .venv\\Scripts\\python.exe app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from src.dashboard.window import main as _window_main


if __name__ == "__main__":
    sys.exit(_window_main())
