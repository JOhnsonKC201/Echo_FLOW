"""Standalone dashboard preview — runs the Flask app against the real
config.yaml + history.db with NO daemon, NO mic, NO hotkeys.

Use:  .venv\Scripts\python.exe scripts\preview_dashboard.py
"""
from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import yaml
from src.history import History
from src.dashboard.server import pick_port, write_port_file
from src.dashboard.app import make_app
from werkzeug.serving import make_server


class _AppRef:
    """Minimal stand-in for src.main.App — only the attrs the dashboard reads."""
    def __init__(self, cfg, cfg_path, history):
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.history = history
        self._scratchpad_target_id = None

    # Optional hooks the dashboard probes via getattr — safe no-ops.
    def reload_config(self):
        pass

    def refresh_transform_hotkeys(self):
        pass


def main() -> int:
    cfg_path = REPO / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    db_path = REPO / cfg.get("history", {}).get("db_path", "data/history.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    history = History(str(db_path))
    app_ref = _AppRef(cfg, cfg_path, history)

    host = cfg.get("dashboard", {}).get("host", "127.0.0.1")
    pref = int(cfg.get("dashboard", {}).get("port", 8766))
    port = pick_port(host, pref)
    write_port_file(port)

    flask_app = make_app(app_ref)
    server = make_server(host, port, flask_app, threaded=True)
    url = f"http://{host}:{port}/"
    print(f"Echo Flow dashboard preview at {url}")
    print("Press Ctrl+C to stop.")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
