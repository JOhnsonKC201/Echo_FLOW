"""Watchdog: relaunches the daemon if it crashes, but not if the user quit.

Lifecycle:
1. The daemon writes its PID to data/wispr.pid at startup and leaves it there
   for its whole life (including across a crash — see src/singleton.py). On a
   deliberate quit it first writes data/wispr.stop.
2. This watchdog polls every 30s and decides via _decide():
   - wispr.stop present  → user quit on purpose → idle (don't relaunch), but
     stay resident: a manual relaunch clears the flag (singleton) and the
     watchdog resumes watching the new daemon.
   - PID present but dead → crash → relaunch via run_silent.vbs.
   - PID alive, or no PID yet during startup → do nothing.
3. A RestartLimiter caps relaunches (MAX_RESTARTS within RESTART_WINDOW_S) so a
   daemon that dies instantly (bad config, missing model) isn't respawned
   forever; when the breaker opens it writes data/wispr.crashloop and stops
   relaunching — but keeps polling, and resumes (fresh limiter, flag cleared)
   once it sees a manually-started daemon alive again.
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


def _clear_crashloop_flag(log):
    """Drop a stale crash-loop marker. Called when watching (re)starts so the
    on-disk state reflects "the watchdog is on duty", not a past breaker trip."""
    try:
        if CRASHLOOP_FLAG.exists():
            CRASHLOOP_FLAG.unlink()
    except Exception as e:
        log.warning("crashloop flag unlink failed: %s", e)


def _run_loop(log, *, sleep=time.sleep, now=time.time, startup_delay: float = 15):
    """The supervision loop. Runs forever; tests inject `sleep` to script ticks
    and break out (any exception from sleep propagates to the caller).

    States:
    - watching: relaunch on crash, idle on stop flag.
    - breaker_open: crash-loop detected; don't relaunch, but keep polling and
      resume watching (fresh limiter) once a daemon is alive again.
    """
    limiter = RestartLimiter()
    breaker_open = False
    idle_logged = False  # log state transitions once, not every 30s tick

    # Give the daemon ~15s to come up before we start checking
    sleep(startup_delay)
    while True:
        pid = _read_pid()
        alive = _is_alive(pid) if pid else False
        action = _decide(STOP_FLAG.exists(), pid, alive)

        if breaker_open:
            # Crash-loop breaker tripped. Never relaunch in this state — wait
            # until the user fixes the cause and manually starts a daemon
            # (which we observe as an alive PID), then resume watching.
            if alive:
                log.info("daemon is back (pid %s) — resuming watch", pid)
                breaker_open = False
                limiter = RestartLimiter()
                _clear_crashloop_flag(log)
            sleep(POLL_SECONDS)
            continue

        if action == "stop":
            # User deliberately quit (tray Quit / Ctrl+C wrote the sentinel).
            # Idle — do NOT relaunch — but stay resident: a manual relaunch
            # clears wispr.stop (singleton._clear_stop_flag) and the next
            # tick resumes normal watching.
            if not idle_logged:
                log.info("stop requested — idling until manual relaunch")
                idle_logged = True
            sleep(POLL_SECONDS)
            continue
        if idle_logged:
            log.info("stop flag cleared — resuming watch")
            idle_logged = False

        if action == "relaunch":
            if not limiter.allow(now()):
                # Breaker opens: the daemon keeps dying. Stop respawning a
                # broken build/config and leave a trace + marker so the
                # user knows recovery was paused (not silently looping).
                log.error(
                    "daemon crash-looped (>=%d restarts in %ds) — pausing "
                    "relaunches; fix the cause and start it manually",
                    MAX_RESTARTS, RESTART_WINDOW_S)
                try:
                    CRASHLOOP_FLAG.write_text("crash-loop; watchdog paused relaunches")
                except Exception:
                    pass
                breaker_open = True
                sleep(POLL_SECONDS)
                continue
            # Daemon was running, now it's dead → relaunch. Unlink first so a
            # slow/failed relaunch reads as "missing" (no retry) rather than
            # "dead" (which would re-trip the breaker on the next poll).
            try:
                PID_FILE.unlink()
            except FileNotFoundError:
                pass  # already gone — exactly the state we want
            except Exception as e:
                # Couldn't clear the stale PID file. Relaunching now would leave
                # it on disk, where a recycled OS PID (Windows reuses PIDs) could
                # later read as "alive" and mask a genuine crash. Defer to the
                # next poll instead of relaunching over stale state.
                log.warning("stale PID unlink failed (%s) — deferring relaunch", e)
                sleep(POLL_SECONDS)
                continue
            log.warning("daemon (pid %s) is dead — relaunching", pid)
            _relaunch()
        sleep(POLL_SECONDS)


def main():
    import logging
    log = logging.getLogger("wispr.watchdog")

    # Sentinel paths are cwd-relative (shared contract with src/singleton.py).
    # run_silent.vbs launches us with cwd = repo root, but Task Scheduler or a
    # debugger may not — normalize so the sentinels always resolve to the same
    # data/ directory the daemon uses.
    if not getattr(sys, "frozen", False):
        os.chdir(Path(__file__).resolve().parent.parent)

    # Single-instance for the watchdog itself
    lock = _acquire_lock()
    if lock is None:
        print("Another watchdog is running. Exiting.")
        return

    WATCHDOG_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    WATCHDOG_PID_FILE.write_text(str(os.getpid()))
    # A fresh watchdog means "watching again" — a crashloop marker from a
    # previous generation is stale by definition.
    _clear_crashloop_flag(log)

    try:
        _run_loop(log)
    finally:
        try:
            WATCHDOG_PID_FILE.unlink()
        except Exception:
            pass
        lock.close()


if __name__ == "__main__":
    main()
