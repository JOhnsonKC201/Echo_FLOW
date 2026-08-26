"""Starting Ollama when it is installed but not running.

Every branch is driven through injected callables, so the suite never spawns a
process, never touches the network, and passes identically on a machine with no
Ollama installed (which is what CI is).
"""
from pathlib import Path

import pytest

from src import ollama_supervisor as sup


class _Probe:
    """Alive-probe that flips to True after `flips_after` calls."""

    def __init__(self, flips_after=None):
        self.calls = 0
        self.flips_after = flips_after

    def __call__(self):
        self.calls += 1
        if self.flips_after is None:
            return False
        return self.calls > self.flips_after


def test_already_running_does_not_look_for_a_binary():
    """The cheap probe short-circuits everything else."""
    def _finder():
        raise AssertionError("must not search for a binary when Ollama is up")

    assert sup.ensure_running(lambda: True, finder=_finder) == "already-running"


def test_not_installed_is_a_clean_outcome(monkeypatch):
    """A machine with no Ollama is a supported configuration, not an error."""
    monkeypatch.setattr(sup, "_spawn", lambda exe: pytest.fail("must not spawn"))
    assert sup.ensure_running(_Probe(), finder=lambda: None) == "not-installed"


def test_starts_and_waits_for_the_port(monkeypatch):
    spawned = []
    monkeypatch.setattr(sup, "_spawn", lambda exe: spawned.append(exe) or True)
    probe = _Probe(flips_after=2)   # down, down, then up

    out = sup.ensure_running(
        probe,
        finder=lambda: Path("ollama app.exe"),
        sleep=lambda s: None,
    )
    assert out == "started"
    assert spawned == [Path("ollama app.exe")]


def test_spawn_failure_is_reported(monkeypatch):
    monkeypatch.setattr(sup, "_spawn", lambda exe: False)
    out = sup.ensure_running(_Probe(), finder=lambda: Path("ollama.exe"))
    assert out == "spawn-failed"


def test_timeout_when_it_never_answers(monkeypatch):
    """A slow or wedged Ollama must not hold the daemon hostage."""
    monkeypatch.setattr(sup, "_spawn", lambda exe: True)
    out = sup.ensure_running(
        _Probe(),                      # never goes green
        timeout_sec=0.05,
        poll_sec=0.01,
        finder=lambda: Path("ollama.exe"),
        sleep=lambda s: None,
    )
    assert out == "timeout"


def test_zero_timeout_does_not_hang(monkeypatch):
    monkeypatch.setattr(sup, "_spawn", lambda exe: True)
    assert sup.ensure_running(
        _Probe(), timeout_sec=0, finder=lambda: Path("x.exe"), sleep=lambda s: None
    ) == "timeout"


# --- discovery --------------------------------------------------------------

def test_finds_the_tray_app_in_localappdata(monkeypatch, tmp_path):
    exe = tmp_path / "Programs" / "Ollama" / "ollama app.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert sup.find_ollama() == exe


def test_prefers_the_tray_app_over_the_bare_server(monkeypatch, tmp_path):
    """The tray app is what the Start Menu shortcut runs, so our spawn looks
    exactly like the user starting it themselves."""
    d = tmp_path / "Programs" / "Ollama"
    d.mkdir(parents=True)
    (d / "ollama app.exe").write_text("")
    (d / "ollama.exe").write_text("")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert sup.find_ollama().name == "ollama app.exe"


def test_falls_back_to_path(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))   # nothing installed there
    monkeypatch.setattr(sup.shutil, "which", lambda n: r"C:\tools\ollama.exe")
    assert sup.find_ollama() == Path(r"C:\tools\ollama.exe")


def test_returns_none_when_nothing_is_installed(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sup.shutil, "which", lambda n: None)
    monkeypatch.setattr(sup, "_EXTRA_DIRS", ())
    assert sup.find_ollama() is None


def test_unreadable_candidate_does_not_break_discovery(monkeypatch, tmp_path):
    """A disconnected drive letter in a candidate path must not take the whole
    probe down, it should just move to the next candidate."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sup, "_candidate_paths", lambda: [_Exploding(), tmp_path / "no.exe"])
    monkeypatch.setattr(sup.shutil, "which", lambda n: None)
    assert sup.find_ollama() is None


class _Exploding:
    def is_file(self):
        raise OSError("drive not ready")


# --- the background watcher -------------------------------------------------

def test_watch_calls_back_once_when_ollama_arrives():
    """The whole point: a cold Ollama that shows up late upgrades the running
    daemon instead of making the user restart it."""
    probe = _Probe(flips_after=2)
    fired = []
    sup.watch_until_ready(
        probe, lambda: fired.append(True),
        timeout_sec=10, poll_sec=0.01, sleep=lambda s: None,
    )
    assert fired == [True]


def test_watch_gives_up_quietly():
    """A machine with no Ollama must not keep a thread polling forever."""
    fired = []
    sup.watch_until_ready(
        _Probe(), lambda: fired.append(True),
        timeout_sec=0.05, poll_sec=0.01, sleep=lambda s: None,
    )
    assert fired == []


def test_watch_survives_a_failing_callback():
    """The callback mutates live app state; a bug there must be logged, not
    swallowed into a dead thread."""
    def _boom():
        raise RuntimeError("upgrade failed")

    # Must not propagate: this runs on a daemon thread with no one to catch it.
    sup.watch_until_ready(
        _Probe(flips_after=0), _boom,
        timeout_sec=1, poll_sec=0.01, sleep=lambda s: None,
    )
