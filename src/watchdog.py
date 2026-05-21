"""Watchdog: relaunches the daemon if it crashes.

Lifecycle:
1. Daemon writes its PID to data/wispr.pid at startup, deletes it on graceful exit.
2. This watchdog polls every 30s:
   - If wispr.pid is missing → daemon hasn't started yet or quit cleanly. Don't restart.
   - If wispr.pid exists but the PID is dead → daemon crashed. Relaunch via run.bat.
   - If wispr.pid exists and the PID is alive → all good.
3. The singleton lock in main.py prevents double-launch if the user manually
   started the daemon while we were about to relaunch.

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
    # Single-instance for the watchdog itself
    lock = _acquire_lock()
    if lock is None:
        print("Another watchdog is running. Exiting.")
        return

    WATCHDOG_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    WATCHDOG_PID_FILE.write_text(str(os.getpid()))

    try:
        # Give the daemon ~15s to come up before we start checking
        time.sleep(15)
        while True:
            pid = _read_pid()
            if pid is not None and not _is_alive(pid):
                # Daemon was running, now it's dead → relaunch
                try:
                    PID_FILE.unlink()
                except Exception as e:
                    import logging
                    logging.getLogger("wispr.watchdog").warning(
                        "stale PID file unlink failed: %s", e)
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
