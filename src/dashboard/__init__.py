"""Echo Flow desktop dashboard — local-only Flask server.

Bound to 127.0.0.1 by design. No auth: the loopback boundary IS the auth.
Same-machine trust model: anyone who can reach this port can already see
config.yaml and inject keystrokes into your windows.

Daemon-thread launcher mirrors src/bridge.py's serve() shape.
"""
from __future__ import annotations

from .server import serve, pick_port, write_port_file, read_port_file

__all__ = ["serve", "pick_port", "write_port_file", "read_port_file"]
