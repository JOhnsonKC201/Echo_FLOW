"""Watchdog: relaunches the daemon if it crashes, but not if the user quit.

Lifecycle:
1. The daemon writes its PID to data/wispr.pid at startup and leaves it there
   for its whole life (including across a crash — see src/singleton.py). On a
   deliberate quit it first writes data/wispr.stop.
2. This watchdog polls every 30s and decides via _decide():
   - wispr.stop present  → user quit on purpose → stand down (stop watching).
   - PID present but dead → crash → relaunch via run_silent.vbs.
   - PID alive, or no PID yet during startup → do nothing.
3. A RestartLimiter caps relaunches (MAX_RESTARTS within RESTART_WINDOW_S) so a
   daemon that dies instantly (bad config, missing model) isn't respawned
   forever; when the breaker opens it writes data/wispr.crashloop and gives up.
4. The singleton lock in main.py prevents double-launch if the user manually
   started the daemon while we were about to relaunch.

Why the stop sentinel instead of "PID missing": the tray-quit path calls
os._exit(), which skips atexit, and an exception crash used to clear the PID via
atexit — so "dead/missing PID" alone could never distinguish a deliberate quit
from a crash. Intent (wispr.stop) and health (PID liveness) are now separate.

The watchdog itself uses a separate PID file (data/watchdog.pid) and the same
single-instance trick on a different port, so only one watchdog runs.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path


WATCHDOG_PORT = 47824
POLL_SECONDS = 30
PID_FILE = Path("data/wispr.pid")
WATCHDOG_PID_FILE = Path("data/watchdog.pid")
STOP_FLAG = Path("data/wispr.stop")        # present ⇒ user quit; do not relaunch
CRASHLOOP_FLAG = Path("data/wispr.crashloop")  # written when the breaker trips

# Crash-loop circuit breaker: never relaunch more than MAX_RESTARTS times within
# RESTART_WINDOW_S. A daemon that dies instantly (corrupt config, missing model)
# would otherwise be respawned every POLL_SECONDS forever.
MAX_RESTARTS = 5
RESTART_WINDOW_S = 600


class RestartLimiter:
    """Sliding-window rate limiter for relaunches.

    allow(now) records the attempt and returns True while under the cap; once
    MAX_RESTARTS land inside RESTART_WINDOW_S it returns False (breaker open).
    Pure and deterministic — `now` is injected so it is unit-testable.
    """

    def __init__(self, limit: int = MAX_RESTARTS, window_s: float = RESTART_WINDOW_S):
        self.limit = limit
        self.window_s = window_s
        self._stamps: list[float] = []

    def allow(self, now: float) -> bool:
        self._stamps = [t for t in self._stamps if now - t < self.window_s]
        if len(self._stamps) >= self.limit:
            return False
        self._stamps.append(now)
        return True


def _decide(stop_requested: bool, pid: int | None, alive: bool) -> str:
    """Pure relaunch decision. Returns 'stop' | 'relaunch' | 'ok'.

    - stop_requested → the user deliberately quit; stand down.
    - pid present but dead, and no stop → the daemon crashed; relaunch.
    - otherwise (alive, or no PID yet during startup) → do nothing.
    """
    if stop_requested:
        return "stop"
    if pid is not None and not alive:
        return "relaunch"
    return "ok"


def _is_alive(pid: int) -> bool:
    """OS-independent check that a process with this PID exists."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(h, ctypes.byref(exit_code)) == 0:
                    return False
                return exit_code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(h)
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def _relaunch():
    """Launch run_silent.vbs to start a fresh daemon (no console window)."""
    cwd = Path(__file__).resolve().parent.parent
    vbs = cwd / "run_silent.vbs"
    if not vbs.exists():
        return
    try:
        subprocess.Popen(
            ["wscript.exe", str(vbs)],
            cwd=str(cwd),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        # If relaunch fails the daemon is dead. Log loudly to wispr.log
        # so the user sees something happened.
        import logging
        logging.getLogger("wispr.watchdog").error("relaunch failed: %s", e)


def _acquire_lock() -> socket.socket | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", WATCHDOG_PORT))
        s.listen(1)
        return s
    except OSError:
        return None


def main():
    import logging
    log = logging.getLogger("wispr.watchdog")

    # Single-instance for the watchdog itself
    lock = _acquire_lock()
    if lock is None:
        print("Another watchdog is running. Exiting.")
        return

    WATCHDOG_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    WATCHDOG_PID_FILE.write_text(str(os.getpid()))
    limiter = RestartLimiter()

    try:
        # Give the daemon ~15s to come up before we start checking
        time.sleep(15)
        while True:
            pid = _read_pid()
            action = _decide(STOP_FLAG.exists(), pid, _is_alive(pid) if pid else False)
            if action == "stop":
                # User deliberately quit (tray Quit / Ctrl+C wrote the sentinel).
                # Stand down so we don't resurrect a daemon they shut off.
                log.info("stop requested — watchdog standing down")
                break
            if action == "relaunch":
                if not limiter.allow(time.time()):
                    # Breaker open: the daemon keeps dying. Stop respawning a
                    # broken build/config and leave a trace + marker so the
                    # user knows recovery was abandoned (not silently looping).
                    log.error(
                        "daemon crash-looped (>=%d restarts in %ds) — giving up; "
                        "fix the cause and relaunch manually",
                        MAX_RESTARTS, RESTART_WINDOW_S)
                    try:
                        CRASHLOOP_FLAG.write_text("crash-loop; watchdog gave up")
                    except Exception:
                        pass
                    break
                # Daemon was running, now it's dead → relaunch. Unlink first so a
                # slow/failed relaunch reads as "missing" (no retry) rather than
                # "dead" (which would re-trip the breaker on the next poll).
                try:
                    PID_FILE.unlink()
                except Exception as e:
                    log.warning("stale PID file unlink failed: %s", e)
                log.warning("daemon (pid %s) is dead — relaunching", pid)
                _relaunch()
            time.sleep(POLL_SECONDS)
    finally:
        try:
            WATCHDOG_PID_FILE.unlink()
        except Exception:
            pass
        lock.close()


if __name__ == "__main__":
    main()
