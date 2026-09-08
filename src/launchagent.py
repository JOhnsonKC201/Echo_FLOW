"""Start at login on macOS through a per-user LaunchAgent.

Windows gets autostart from the installer (an HKCU Run key). A Mac running
from source gets it from ``~/Library/LaunchAgents/com.echoflow.daemon.plist``,
which tells launchd to run ``.venv/bin/python -m src.main`` from the repo at
login and to start it again if it ever dies with a non-zero status. A quit
from the tray exits 0 and stays quit, so launchd also takes over the job the
watchdog does on Windows.

Everything goes through ``launchctl``; nothing here needs root. The scripts
``scripts/install_autostart.sh``, ``scripts/uninstall_autostart.sh`` and
``restart.sh`` are thin wrappers over the functions below, and every function
takes ``platform``, ``home`` and ``run`` so the whole module is testable on
any OS without touching launchd.

One thing to know: when launchd starts the daemon, macOS attributes the
Input Monitoring, Microphone and Accessibility grants to the Python
interpreter itself rather than to Terminal, so the first login start asks for
them again. The startup permission report lands in ``logs/launchd.out.log``.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from . import hostos

LABEL = "com.echoflow.daemon"

# launchd starts agents with a bare PATH; Homebrew and /usr/local are where the
# Ollama CLI lives when it was not installed as an app.
_AGENT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"


def plist_path(home: Path | None = None) -> Path:
    """Where the agent lives for this user."""
    return Path(home or Path.home()) / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def render_plist(repo: Path, python: Path, label: str = LABEL) -> bytes:
    """The LaunchAgent that runs the daemon from ``repo`` at login.

    ``KeepAlive.SuccessfulExit = false`` is the crash policy: relaunch after a
    non-zero exit, leave a deliberate quit alone. ``WorkingDirectory`` matters
    because the daemon keeps everything (config.yaml, data/, logs/) relative
    to the repo root.
    """
    repo = Path(repo)
    doc = {
        "Label": label,
        "ProgramArguments": [str(python), "-m", "src.main"],
        "WorkingDirectory": str(repo),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Interactive",
        "StandardOutPath": str(repo / "logs" / "launchd.out.log"),
        "StandardErrorPath": str(repo / "logs" / "launchd.err.log"),
        "EnvironmentVariables": {
            "PATH": _AGENT_PATH,
            "PYTHONUNBUFFERED": "1",
        },
    }
    return plistlib.dumps(doc)


def _current_uid() -> int:
    return int(getattr(os, "getuid", lambda: 0)())


def _launchctl(run, args: list[str]) -> subprocess.CompletedProcess:
    """One launchctl call, captured, never raising."""
    try:
        return run(["launchctl", *args], capture_output=True, text=True, timeout=30)
    except Exception as e:  # launchctl missing, timeout: report as a failure
        return subprocess.CompletedProcess(["launchctl", *args], 1, "", str(e))


def _stderr(done: subprocess.CompletedProcess) -> str:
    return (done.stderr or done.stdout or "").strip()


def is_installed(home: Path | None = None) -> bool:
    return plist_path(home).exists()


def install(repo: Path, *, home: Path | None = None, platform: str | None = None,
            run=subprocess.run, uid: int | None = None) -> tuple[bool, str]:
    """Write the agent and hand it to launchd. Starts the daemon right away.

    Returns (ok, message). Refuses off macOS and before ``scripts/setup.sh``
    has created the venv, because the plist would point at nothing.
    """
    if not hostos.is_mac(platform):
        return False, "Start at login through a LaunchAgent only exists on macOS."
    repo = Path(repo).resolve()
    python = hostos.venv_python(repo, platform)
    if python is None:
        return False, "No .venv yet. Run scripts/setup.sh first, then install autostart."
    uid = _current_uid() if uid is None else uid
    path = plist_path(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        (repo / "logs").mkdir(parents=True, exist_ok=True)
        # A copy from an earlier install may still be loaded; bootstrap refuses
        # to load the same label twice, so unload first and ignore "not loaded".
        _launchctl(run, ["bootout", f"gui/{uid}/{LABEL}"])
        path.write_bytes(render_plist(repo, python))
    except OSError as e:
        return False, f"Could not write {path}: {e}"
    done = _launchctl(run, ["bootstrap", f"gui/{uid}", str(path)])
    if done.returncode != 0:
        return False, f"launchctl bootstrap failed: {_stderr(done) or done.returncode}"
    return True, (f"Installed {path}. Echo Flow starts at login and is running now; "
                  f"its console is logs/launchd.out.log.")


def uninstall(*, home: Path | None = None, platform: str | None = None,
              run=subprocess.run, uid: int | None = None) -> tuple[bool, str]:
    """Stop the agent and remove the plist. Fine to call when nothing is installed."""
    if not hostos.is_mac(platform):
        return False, "Start at login through a LaunchAgent only exists on macOS."
    uid = _current_uid() if uid is None else uid
    path = plist_path(home)
    _launchctl(run, ["bootout", f"gui/{uid}/{LABEL}"])
    if not path.exists():
        return True, "Autostart was not installed."
    try:
        path.unlink()
    except OSError as e:
        return False, f"Could not remove {path}: {e}"
    return True, f"Removed {path}. Echo Flow no longer starts at login."


def restart(*, home: Path | None = None, platform: str | None = None,
            run=subprocess.run, uid: int | None = None) -> tuple[bool, str]:
    """Kill and relaunch the daemon through launchd. Only when installed."""
    if not hostos.is_mac(platform):
        return False, "Start at login through a LaunchAgent only exists on macOS."
    if not is_installed(home):
        return False, "Autostart is not installed, so launchd is not running Echo Flow."
    uid = _current_uid() if uid is None else uid
    done = _launchctl(run, ["kickstart", "-k", f"gui/{uid}/{LABEL}"])
    if done.returncode != 0:
        return False, f"launchctl kickstart failed: {_stderr(done) or done.returncode}"
    return True, "Restarted Echo Flow through launchd."


def status(*, home: Path | None = None, platform: str | None = None,
           run=subprocess.run, uid: int | None = None) -> tuple[bool, str]:
    """Is the agent installed, and does launchd currently know about it?"""
    if not hostos.is_mac(platform):
        return False, "Start at login through a LaunchAgent only exists on macOS."
    path = plist_path(home)
    if not path.exists():
        return False, "Autostart is not installed."
    uid = _current_uid() if uid is None else uid
    done = _launchctl(run, ["print", f"gui/{uid}/{LABEL}"])
    if done.returncode != 0:
        return True, f"Installed at {path} but not loaded; log in again or run install."
    return True, f"Installed at {path} and loaded."


_COMMANDS = {
    "install": lambda repo, **kw: install(repo, **kw),
    "uninstall": lambda repo, **kw: uninstall(**kw),
    "restart": lambda repo, **kw: restart(**kw),
    "status": lambda repo, **kw: status(**kw),
}


def main(argv: list[str] | None = None, *, repo: Path | None = None,
         platform: str | None = None, home: Path | None = None,
         run=subprocess.run, uid: int | None = None) -> int:
    """``python -m src.launchagent install|uninstall|restart|status``."""
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv else ""
    if command not in _COMMANDS:
        print("usage: python -m src.launchagent install|uninstall|restart|status")
        return 2
    repo = Path(__file__).resolve().parent.parent if repo is None else Path(repo)
    ok, message = _COMMANDS[command](repo, home=home, platform=platform, run=run, uid=uid)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
