"""Tests for the watchdog supervision loop (src/watchdog._run_loop).

The loop is the part the unit tests in test_watchdog.py deliberately skipped —
and exactly where two real bugs lived: the watchdog used to exit permanently on
a deliberate quit (so a later manual relaunch ran unguarded), and the
wispr.crashloop sentinel was never cleared. These tests script the loop tick by
tick via an injected sleep() and assert the resident-guardian behavior:
relaunch on crash, idle (not exit) on stop, pause (not exit) on crash-loop,
and resume in both cases once the daemon is back.
"""
from __future__ import annotations

import logging
import os

import pytest

from src import singleton, watchdog


class _LoopExit(Exception):
    """Raised by the scripted sleep to break out of the infinite loop."""


def _log():
    return logging.getLogger("test.watchdog")


def _setup_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(watchdog, "PID_FILE", tmp_path / "wispr.pid")
    monkeypatch.setattr(watchdog, "STOP_FLAG", tmp_path / "wispr.stop")
    monkeypatch.setattr(watchdog, "CRASHLOOP_FLAG", tmp_path / "wispr.crashloop")


class _ScriptedSleep:
    """Runs a callback per sleep() call; raises _LoopExit when the script ends.

    Call 1 is the startup-delay sleep; each later call is the end of one poll
    iteration, so step N (0-indexed from the second call) runs *after* the
    loop body has executed N+1 times.
    """

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = 0

    def __call__(self, _seconds):
        if not self.steps:
            raise _LoopExit
        step = self.steps.pop(0)
        if step is not None:
            step()
        self.calls += 1


# --- crash → relaunch ---------------------------------------------------------

def test_loop_relaunches_dead_daemon(monkeypatch, tmp_path):
    """PID present but dead, no stop flag → relaunch once, PID file unlinked."""
    _setup_paths(monkeypatch, tmp_path)
    watchdog.PID_FILE.write_text("4242")
    monkeypatch.setattr(watchdog, "_is_alive", lambda pid: False)
    relaunches = []
    monkeypatch.setattr(watchdog, "_relaunch", lambda: relaunches.append(1))

    sleep = _ScriptedSleep([None])  # startup tick, then exit after iteration 1
    with pytest.raises(_LoopExit):
        watchdog._run_loop(_log(), sleep=sleep, startup_delay=0)

    assert relaunches == [1]
    assert not watchdog.PID_FILE.exists(), \
        "stale PID must be unlinked so a slow relaunch reads as 'missing'"


def test_loop_does_nothing_while_daemon_alive(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)
    watchdog.PID_FILE.write_text("4242")
    monkeypatch.setattr(watchdog, "_is_alive", lambda pid: True)
    relaunches = []
    monkeypatch.setattr(watchdog, "_relaunch", lambda: relaunches.append(1))

    sleep = _ScriptedSleep([None, None, None])
    with pytest.raises(_LoopExit):
        watchdog._run_loop(_log(), sleep=sleep, startup_delay=0)

    assert relaunches == []
    assert watchdog.PID_FILE.exists()


# --- deliberate quit → idle, then resume ---------------------------------------

def test_loop_stays_resident_on_stop_and_resumes(monkeypatch, tmp_path):
    """The watchdog must NOT exit on a deliberate quit. It idles while
    wispr.stop is present and resumes watching once a relaunched daemon
    clears it — a crash after that point is recovered again."""
    _setup_paths(monkeypatch, tmp_path)
    watchdog.PID_FILE.write_text("4242")
    watchdog.STOP_FLAG.write_text("4242")
    monkeypatch.setattr(watchdog, "_is_alive", lambda pid: False)
    relaunches = []
    monkeypatch.setattr(watchdog, "_relaunch", lambda: relaunches.append(1))

    def clear_stop():
        # Simulates singleton._clear_stop_flag() on a manual daemon relaunch.
        assert relaunches == [], "must not relaunch while the stop flag is set"
        watchdog.STOP_FLAG.unlink()

    # startup → iter1 (stop: idle) → clear flag → iter2 (dead pid: relaunch)
    sleep = _ScriptedSleep([None, clear_stop])
    with pytest.raises(_LoopExit):
        watchdog._run_loop(_log(), sleep=sleep, startup_delay=0)

    assert relaunches == [1], "watchdog must resume watching after the quit"


# --- crash-loop breaker → pause, then resume ------------------------------------

class _StubLimiter:
    def __init__(self, allows: bool):
        self.allows = allows

    def allow(self, now: float) -> bool:
        return self.allows


def test_loop_breaker_pauses_then_resumes_when_daemon_returns(monkeypatch, tmp_path):
    """When the breaker opens: write wispr.crashloop, stop relaunching — but
    keep polling. Once a manually-fixed daemon is alive again, clear the flag,
    reset the limiter, and recover the next crash normally."""
    _setup_paths(monkeypatch, tmp_path)
    watchdog.PID_FILE.write_text("4242")
    alive = {"v": False}
    monkeypatch.setattr(watchdog, "_is_alive", lambda pid: alive["v"])
    relaunches = []
    monkeypatch.setattr(watchdog, "_relaunch", lambda: relaunches.append(1))
    # First limiter trips immediately (breaker opens); the post-resume limiter
    # allows again — proving the loop made a fresh one.
    limiters = iter([_StubLimiter(False), _StubLimiter(True)])
    monkeypatch.setattr(watchdog, "RestartLimiter", lambda *a, **k: next(limiters))

    def after_breaker_tripped():
        assert watchdog.CRASHLOOP_FLAG.exists(), "breaker must leave a marker"
        assert relaunches == []
        alive["v"] = True  # user fixed the cause and started the daemon

    def after_resume():
        assert not watchdog.CRASHLOOP_FLAG.exists(), \
            "marker must be cleared once the daemon is back"
        alive["v"] = False  # it crashes again

    # startup → iter1 (dead, limiter blocks → breaker) → iter2 (alive → resume)
    # → iter3 (dead again → relaunch with the fresh limiter)
    sleep = _ScriptedSleep([None, after_breaker_tripped, after_resume])
    with pytest.raises(_LoopExit):
        watchdog._run_loop(_log(), sleep=sleep, startup_delay=0)

    assert relaunches == [1]


def test_clear_crashloop_flag_removes_stale_marker(monkeypatch, tmp_path):
    """A fresh watchdog generation clears a crashloop marker left by a previous
    one — the marker means 'recovery paused', which is no longer true."""
    _setup_paths(monkeypatch, tmp_path)
    watchdog.CRASHLOOP_FLAG.write_text("stale")
    watchdog._clear_crashloop_flag(_log())
    assert not watchdog.CRASHLOOP_FLAG.exists()
    # Missing flag is a no-op, not an error.
    watchdog._clear_crashloop_flag(_log())


# --- request_stop durability ----------------------------------------------------

def test_request_stop_writes_flag_durably(monkeypatch, tmp_path):
    """request_stop is immediately followed by os._exit() on the tray-quit
    path; the sentinel must be fully written (fsync'd) — verify content, not
    just existence, so a truncated write would fail here."""
    flag = tmp_path / "wispr.stop"
    monkeypatch.setattr(singleton, "_STOP_FLAG", flag)
    singleton.request_stop()
    assert flag.read_text() == str(os.getpid())
