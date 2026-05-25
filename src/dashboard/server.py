"""Dashboard server entry point — Flask + werkzeug daemon thread.

Mirrors src/bridge.py:serve() so the operational shape is identical:
- Owns the werkzeug make_server lifecycle
- Logs a single banner on start
- Crashes are caught + logged; the daemon stays up
"""
from __future__ import annotations

import socket
from pathlib import Path

from .. import log as wlog

_log = wlog.get("dashboard")
_active_server = None


_PORT_FILE = Path("data") / "dashboard.port"


def pick_port(host: str, preferred: int, attempts: int = 5) -> int:
    """Return the first free port starting at `preferred`.

    Mirrors the spirit of bridge.py's tolerance for occupied ports — we never
    want a port collision to wedge daemon startup.
    """
    for offset in range(attempts):
        candidate = preferred + offset
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((host, candidate))
            s.close()
            return candidate
        except OSError:
            try:
                s.close()
            except Exception:
                pass
            continue
    # All preferred ports busy; let OS pick. Best-effort.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, 0))
        chosen = s.getsockname()[1]
    finally:
        s.close()
    return chosen


def write_port_file(port: int, path: Path = _PORT_FILE) -> None:
    """Persist the actual bound port so the tray launcher can find it."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(port), encoding="utf-8")
    except Exception as e:
        _log.warning("could not write port file %s: %s", path, e)


def read_port_file(path: Path = _PORT_FILE) -> int | None:
    """Read the bound port written by a running daemon, or None."""
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def serve(app_ref, host: str, port: int, log_fn=None) -> None:
    """Run the dashboard server in the current thread; caller spawns it as a daemon.

    Port-scans from `port` upward (5 attempts) before binding.
    Writes the chosen port to data/dashboard.port.
    """
    global _active_server
    bind_port = pick_port(host, port)
    write_port_file(bind_port)

    from .app import make_app
    from werkzeug.serving import make_server

    flask_app = make_app(app_ref)
    server = make_server(host, bind_port, flask_app, threaded=True)
    _active_server = server
    banner = (
        f"[green]Dashboard: http://{host}:{bind_port}[/green]  "
        f"[dim]Open via tray > Open Dashboard[/dim]"
    )
    if log_fn:
        log_fn(banner)
    else:
        _log.info("dashboard listening on %s:%s", host, bind_port)
    try:
        server.serve_forever()
    except Exception as e:
        _log.error("dashboard server crashed: %s", e)


def shutdown() -> None:
    """Stop the active server, if any. Used by tests and clean shutdown."""
    global _active_server
    if _active_server is not None:
        try:
            _active_server.shutdown()
        except Exception:
            pass
        _active_server = None
