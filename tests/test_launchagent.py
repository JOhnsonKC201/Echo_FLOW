"""src/launchagent.py: start at login on macOS, without touching launchd.

Every call takes an explicit platform, home and a fake ``run``, so the file
passes on Windows and on the macOS CI job alike.
"""
from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import pytest

from src import launchagent as la


class FakeLaunchctl:
    """Records every launchctl call and answers with scripted return codes."""

    def __init__(self, codes: dict[str, int] | None = None):
        self.calls: list[list[str]] = []
        self.codes = codes or {}

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        verb = argv[1]
        code = self.codes.get(verb, 0)
        return subprocess.CompletedProcess(argv, code, "", f"{verb} said no" if code else "")

    def verbs(self) -> list[str]:
        return [c[1] for c in self.calls]


def _repo_with_venv(tmp_path: Path) -> Path:
    repo = tmp_path / "Echo_FLOW"
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python").write_text("")
    return repo


# --- the plist ---------------------------------------------------------------------

def test_render_plist_runs_the_daemon_from_the_repo(tmp_path):
    repo = tmp_path / "Echo_FLOW"
    doc = plistlib.loads(la.render_plist(repo, repo / ".venv" / "bin" / "python"))
    assert doc["Label"] == "com.echoflow.daemon"
    assert doc["ProgramArguments"] == [str(repo / ".venv" / "bin" / "python"), "-m", "src.main"]
    assert doc["WorkingDirectory"] == str(repo)
    assert doc["RunAtLoad"] is True
    assert doc["StandardOutPath"] == str(repo / "logs" / "launchd.out.log")
    assert doc["StandardErrorPath"] == str(repo / "logs" / "launchd.err.log")


def test_render_plist_relaunches_crashes_but_not_quits(tmp_path):
    doc = plistlib.loads(la.render_plist(tmp_path, tmp_path / "python"))
    assert doc["KeepAlive"] == {"SuccessfulExit": False}


def test_render_plist_gives_launchd_a_path_with_homebrew(tmp_path):
    doc = plistlib.loads(la.render_plist(tmp_path, tmp_path / "python"))
    assert "/opt/homebrew/bin" in doc["EnvironmentVariables"]["PATH"]
    assert "/usr/local/bin" in doc["EnvironmentVariables"]["PATH"]


def test_plist_path_is_the_users_launch_agents_folder(tmp_path):
    assert la.plist_path(tmp_path) == tmp_path / "Library" / "LaunchAgents" / "com.echoflow.daemon.plist"


# --- install -----------------------------------------------------------------------

def test_install_refuses_off_mac(tmp_path):
    ok, msg = la.install(tmp_path, home=tmp_path, platform="win32", run=FakeLaunchctl())
    assert ok is False and "macOS" in msg


def test_install_refuses_without_a_venv(tmp_path):
    fake = FakeLaunchctl()
    ok, msg = la.install(tmp_path, home=tmp_path, platform="darwin", run=fake, uid=501)
    assert ok is False and "setup.sh" in msg
    assert fake.calls == []


def test_install_writes_the_plist_and_bootstraps_it(tmp_path):
    repo = _repo_with_venv(tmp_path)
    home = tmp_path / "home"
    fake = FakeLaunchctl()
    ok, msg = la.install(repo, home=home, platform="darwin", run=fake, uid=501)
    assert ok is True, msg
    path = la.plist_path(home)
    assert path.exists()
    doc = plistlib.loads(path.read_bytes())
    assert doc["WorkingDirectory"] == str(repo.resolve())
    assert (repo / "logs").is_dir()
    # An older copy is unloaded first, then the fresh file is loaded.
    assert fake.verbs() == ["bootout", "bootstrap"]
    assert fake.calls[0] == ["launchctl", "bootout", "gui/501/com.echoflow.daemon"]
    assert fake.calls[1] == ["launchctl", "bootstrap", "gui/501", str(path)]
    assert "launchd.out.log" in msg


def test_install_survives_a_failed_bootout_of_nothing(tmp_path):
    repo = _repo_with_venv(tmp_path)
    fake = FakeLaunchctl(codes={"bootout": 3})   # "No such process"
    ok, _ = la.install(repo, home=tmp_path / "h", platform="darwin", run=fake, uid=501)
    assert ok is True


def test_install_reports_a_bootstrap_failure(tmp_path):
    repo = _repo_with_venv(tmp_path)
    fake = FakeLaunchctl(codes={"bootstrap": 5})
    ok, msg = la.install(repo, home=tmp_path / "h", platform="darwin", run=fake, uid=501)
    assert ok is False
    assert "bootstrap failed" in msg and "bootstrap said no" in msg


def test_install_reports_a_missing_launchctl(tmp_path):
    repo = _repo_with_venv(tmp_path)

    def no_launchctl(argv, **kw):
        raise FileNotFoundError("launchctl")

    ok, msg = la.install(repo, home=tmp_path / "h", platform="darwin", run=no_launchctl, uid=501)
    assert ok is False and "launchctl" in msg


# --- uninstall ---------------------------------------------------------------------

def test_uninstall_boots_out_and_removes_the_plist(tmp_path):
    repo = _repo_with_venv(tmp_path)
    home = tmp_path / "home"
    la.install(repo, home=home, platform="darwin", run=FakeLaunchctl(), uid=501)
    fake = FakeLaunchctl()
    ok, msg = la.uninstall(home=home, platform="darwin", run=fake, uid=501)
    assert ok is True and "no longer starts at login" in msg
    assert not la.plist_path(home).exists()
    assert fake.calls == [["launchctl", "bootout", "gui/501/com.echoflow.daemon"]]


def test_uninstall_is_fine_when_nothing_is_installed(tmp_path):
    fake = FakeLaunchctl(codes={"bootout": 3})
    ok, msg = la.uninstall(home=tmp_path, platform="darwin", run=fake, uid=501)
    assert ok is True and "not installed" in msg


def test_uninstall_refuses_off_mac(tmp_path):
    ok, _ = la.uninstall(home=tmp_path, platform="linux", run=FakeLaunchctl())
    assert ok is False


# --- restart and status ------------------------------------------------------------

def test_restart_kickstarts_the_loaded_agent(tmp_path):
    repo = _repo_with_venv(tmp_path)
    home = tmp_path / "home"
    la.install(repo, home=home, platform="darwin", run=FakeLaunchctl(), uid=501)
    fake = FakeLaunchctl()
    ok, msg = la.restart(home=home, platform="darwin", run=fake, uid=501)
    assert ok is True and "Restarted" in msg
    assert fake.calls == [["launchctl", "kickstart", "-k", "gui/501/com.echoflow.daemon"]]


def test_restart_declines_when_not_installed(tmp_path):
    fake = FakeLaunchctl()
    ok, msg = la.restart(home=tmp_path, platform="darwin", run=fake, uid=501)
    assert ok is False and "not installed" in msg
    assert fake.calls == []


def test_status_distinguishes_installed_from_loaded(tmp_path):
    repo = _repo_with_venv(tmp_path)
    home = tmp_path / "home"
    assert la.status(home=home, platform="darwin", run=FakeLaunchctl(), uid=501) == (
        False, "Autostart is not installed.")
    la.install(repo, home=home, platform="darwin", run=FakeLaunchctl(), uid=501)
    ok, msg = la.status(home=home, platform="darwin", run=FakeLaunchctl(), uid=501)
    assert ok is True and msg.endswith("and loaded.")
    ok, msg = la.status(home=home, platform="darwin", run=FakeLaunchctl(codes={"print": 113}), uid=501)
    assert ok is True and "not loaded" in msg


# --- the command line --------------------------------------------------------------

def test_main_usage_on_unknown_command(capsys):
    assert la.main([]) == 2
    assert la.main(["dance"]) == 2
    assert "usage" in capsys.readouterr().out


def test_main_install_then_status_then_uninstall(tmp_path, capsys):
    repo = _repo_with_venv(tmp_path)
    home = tmp_path / "home"
    common = dict(repo=repo, home=home, platform="darwin", run=FakeLaunchctl(), uid=501)
    assert la.main(["install"], **common) == 0
    assert la.main(["status"], **common) == 0
    assert la.main(["uninstall"], **common) == 0
    assert la.main(["status"], **common) == 1
    out = capsys.readouterr().out
    assert "Installed" in out and "Removed" in out and "not installed" in out


def test_main_refuses_off_mac(tmp_path, capsys):
    assert la.main(["install"], repo=tmp_path, home=tmp_path, platform="win32", run=FakeLaunchctl()) == 1
    assert "macOS" in capsys.readouterr().out
