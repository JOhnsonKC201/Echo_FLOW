"""Single-instance lock + run/stop intent for the watchdog.

The lock is a TCP socket bound to localhost — if another process holds it, we
exit immediately. The OS releases the port when the process dies, so there is no
stale lock file to clean up.

Two on-disk files coordinate with the watchdog (src/watchdog.py):

  data/wispr.pid   — this daemon's PID. Written at startup and left in place for
                     the process lifetime, INCLUDING across a crash. The watchdog
                     reads it to tell "alive" from "died". It is intentionally NOT
                     cleared on exit: a crash must remain detectable.
  data/wispr.stop  — intent sentinel. Present ⇒ "the user asked to stop; do not
                     relaunch." Written on a deliberate quit, cleared on startup.

This split lets the watchdog distinguish a deliberate quit (stop flag present →
leave it down) from a crash (no stop flag, PID dead → relaunch). Before, both
looked identical: a dead/missing PID. os._exit() in the tray-quit path skips
atexit entirely, so the old atexit-based PID cleanup could never have made that
distinction reliably anyway.
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

from . import log as wlog

_log = wlog.get("singleton")

# Random high port — must match across all instances
_LOCK_PORT = 47823
_lock_socket: socket.socket | None = None
_PID_FILE = Path("data/wispr.pid")
_STOP_FLAG = Path("data/wispr.stop")


def _write_pid():
    try:
        _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PID_FILE.write_text(str(os.getpid()))
    except Exception as e:
        _log.exception(f"Suppressed in _write_pid: {e}")


def _clear_stop_flag():
    """A fresh start means 'this daemon should be running' — drop any stop
    sentinel left by a previous deliberate quit so the watchdog watches us."""
    try:
        if _STOP_FLAG.exists():
            _STOP_FLAG.unlink()
    except Exception as e:
        _log.exception(f"Suppressed in _clear_stop_flag: {e}")


def request_stop():
    """Record that this shutdown is deliberate, so the watchdog does NOT
    relaunch the daemon. Call this on every intentional quit path BEFORE
    exiting (including os._exit(), which skips atexit handlers)."""
    try:
        _STOP_FLAG.parent.mkdir(parents=True, exist_ok=True)
        # The tray-quit path calls os._exit() immediately after this, which
        # does not drain OS write buffers — fsync so the sentinel is durably
        # on disk first, or the watchdog would resurrect a daemon the user
        # just quit.
        with open(_STOP_FLAG, "w") as f:
            f.write(str(os.getpid()))
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        _log.exception(f"Suppressed in request_stop: {e}")


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
    _clear_stop_flag()
