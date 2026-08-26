"""Start Ollama when it is installed but not running.

Echo Flow registers itself in the Startup folder; Ollama registers nowhere. So
on a normal Windows login the daemon comes up with its model backend down, and
every dictation until the user notices lands in the LLM-free path. The user is
then told to "start Ollama and restart Echo Flow", which is a chore we can do
for them: the binary is already on disk, we know where, and bringing it up is
one spawn plus a poll.

Scope is deliberately narrow:

  - We only ever start a backend the user already installed. Nothing is
    downloaded, nothing is installed, and a machine with no Ollama stays a
    machine with no Ollama (that is what the LLM-free path in `fillers.py` and
    `Cleaner._via_learned` is for).
  - We never start a *second* copy. The alive-probe runs first, and Ollama
    itself refuses the port if another instance holds it, so the worst case is
    a spawned process that exits on its own.
  - The wait is bounded. Startup blocks for at most `timeout_sec`; if the
    server is slow the daemon carries on in LLM-free mode and the periodic
    re-probe picks it up later.

Everything here is pure enough to test without Ollama installed: discovery and
the wait loop take injectable callables.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterable

import requests

_log = logging.getLogger(__name__)

# Ollama's Windows installer is per-user and lands in LOCALAPPDATA. "ollama
# app.exe" is the tray application the Start Menu shortcut points at; it starts
# the server and gives the user the tray icon they expect to see. "ollama.exe
# serve" is the headless server. Prefer the tray app so our spawn looks exactly
# like the user starting it themselves, and fall back to the server binary.
_TRAY_RELATIVE = Path("Programs") / "Ollama" / "ollama app.exe"
_SERVER_RELATIVE = Path("Programs") / "Ollama" / "ollama.exe"

# Machine-wide installs, plus the Program Files layout some builds use.
_EXTRA_DIRS = (
    Path(r"C:\Program Files\Ollama"),
    Path(r"C:\Program Files (x86)\Ollama"),
)


def is_alive(base_url: str, timeout: float = 1.0) -> bool:
    """True when an Ollama server answers at `base_url`.

    Kept here rather than in `phase` so the supervisor owns the whole "is the
    backend up" question and `main` does not have to reach into another
    module's private helper to ask it.
    """
    try:
        r = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout)
        return r.status_code == 200
    except requests.exceptions.RequestException as e:
        # Unreachable is the answer this function exists to return, not a fault.
        # A full traceback here floods the error stream on every restart.
        _log.debug("Ollama not reachable at %s: %s", base_url, e)
        return False
    except Exception as e:
        # Anything that is not a network error is genuinely unexpected.
        _log.exception("unexpected error probing Ollama: %s", e)
        return False


def _candidate_paths() -> Iterable[Path]:
    """Every place an installed Ollama might be, most likely first."""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        base = Path(local)
        yield base / _TRAY_RELATIVE
        yield base / _SERVER_RELATIVE
    for d in _EXTRA_DIRS:
        yield d / "ollama app.exe"
        yield d / "ollama.exe"


def find_ollama() -> Path | None:
    """Locate an installed Ollama executable, or None.

    Checks the known install locations first, then PATH. Returns the tray app
    in preference to the bare server when both exist.
    """
    for p in _candidate_paths():
        try:
            if p.is_file():
                return p
        except OSError:
            # A malformed LOCALAPPDATA or a disconnected drive letter should
            # not take the whole probe down.
            continue
    which = shutil.which("ollama")
    return Path(which) if which else None


def _spawn(exe: Path) -> bool:
    """Launch Ollama detached. True if the spawn itself succeeded.

    Detached because Ollama should outlive a daemon restart the same way it
    would if the user had launched it: killing Echo Flow must not kill the
    model backend other things may now be using.
    """
    # The tray app starts the server itself; the bare binary needs `serve`.
    args = [str(exe)] if exe.name.lower() == "ollama app.exe" else [str(exe), "serve"]
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, and no console window.
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(args, **kwargs)
        return True
    except Exception as e:
        # A spawn failure is a real, actionable problem (bad path, blocked by
        # policy, missing DLL) and must not be silent: the user's next signal
        # would otherwise be unexplained LLM-free output.
        _log.warning("could not start Ollama at %s: %s", exe, e)
        return False


def ensure_running(
    is_alive: Callable[[], bool],
    *,
    timeout_sec: float = 8.0,
    poll_sec: float = 0.5,
    finder: Callable[[], "Path | None"] = find_ollama,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Bring Ollama up if it is installed but down. Returns what happened.

    One of:
      ``"already-running"``  the probe answered before we did anything
      ``"started"``          we spawned it and the probe went green
      ``"not-installed"``    no Ollama binary on this machine
      ``"spawn-failed"``     the binary is there but would not launch
      ``"timeout"``          spawned, but not answering inside the budget

    `is_alive` is injected rather than imported so this module stays free of
    the config and HTTP layers, and so tests can drive every branch without a
    network or an install.
    """
    if is_alive():
        return "already-running"

    exe = finder()
    if exe is None:
        _log.debug("Ollama is not installed; staying on the LLM-free path")
        return "not-installed"

    _log.info("Ollama is installed but not running; starting %s", exe)
    if not _spawn(exe):
        return "spawn-failed"

    deadline = time.monotonic() + max(0.0, timeout_sec)
    while time.monotonic() < deadline:
        sleep(poll_sec)
        if is_alive():
            _log.info("Ollama came up")
            return "started"
    _log.warning(
        "Ollama did not answer within %.0fs; continuing without it", timeout_sec
    )
    return "timeout"


def watch_until_ready(
    is_alive: Callable[[], bool],
    on_ready: Callable[[], None],
    *,
    timeout_sec: float = 180.0,
    poll_sec: float = 3.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Poll until Ollama answers, then call `on_ready` exactly once.

    A cold `ollama app.exe` start was measured at over 25 seconds on a real
    machine, which is far too long to block daemon startup for. So the startup
    path waits only long enough to catch a warm start and hands the rest to
    this watcher: the daemon comes up immediately in rules-only mode and
    upgrades itself the moment the backend is ready.

    That also retires the "then restart Echo Flow" instruction the degraded
    toast used to end with. Runs to `timeout_sec` and then gives up quietly;
    a machine with no Ollama should not have a thread polling forever.
    """
    deadline = time.monotonic() + max(0.0, timeout_sec)
    while time.monotonic() < deadline:
        sleep(poll_sec)
        if is_alive():
            _log.info("Ollama became reachable; upgrading cleanup")
            try:
                on_ready()
            except Exception as e:
                # The callback flips app state; a failure there must not kill
                # the thread silently.
                _log.warning("Ollama-ready callback failed: %s", e)
            return
    _log.debug("Ollama did not become reachable within %.0fs", timeout_sec)
