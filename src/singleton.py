"""Single-instance lock. Prevents multiple daemons running at once.

Uses a TCP socket bound to localhost as the lock — if another process holds it,
we exit immediately. The OS releases the port when the process dies, so no
stale lock files to clean up.

Also writes data/wispr.pid for the watchdog to monitor.
"""
from __future__ import annotations

import atexit
import os
import socket
import sys
from pathlib import Path

# Random high port — must match across all instances
_LOCK_PORT = 47823
_lock_socket: socket.socket | None = None
_PID_FILE = Path("data/wispr.pid")


def _write_pid():
    try:
        _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PID_FILE.write_text(str(os.getpid()))
    except Exception:
        pass


def _clear_pid():
    try:
        if _PID_FILE.exists():
            _PID_FILE.unlink()
    except Exception:
        pass


def acquire_or_exit() -> None:
    """If another instance is already running, print a message and exit."""
    global _lock_socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", _LOCK_PORT))
        s.listen(1)
        _lock_socket = s   # keep reference so GC doesn't release it
    except OSError:
        print("Another Echo Flow instance is already running. Exiting.")
        sys.exit(0)
    _write_pid()
    atexit.register(_clear_pid)
