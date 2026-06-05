"""Tests for the auto-restart watchdog's pure logic.

The watchdog relaunches the daemon when it crashes — if its liveness or
PID-parsing logic is wrong, a crashed daemon stays dead (no dictation) or a
healthy one gets double-launched. These cover the testable, side-effect-free
pieces; the poll loop and process relaunch are left to manual/integration use.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

from src import watchdog


# --- _is_alive ---------------------------------------------------------------

def test_is_alive_true_for_current_process():
    """The test process itself is, definitionally, alive."""
    assert watchdog._is_alive(os.getpid()) is True


def test_is_alive_false_for_nonpositive_pids():
    """PID 0 / negative are never real processes — and must not be probed
    (on POSIX, kill(0, 0) signals the whole process group; on Windows
    OpenProcess(0) can alias the System Idle Process). The guard returns
    False without touching the OS."""
    assert watchdog._is_alive(0) is False
    assert watchdog._is_alive(-1) is False


def test_is_alive_false_for_dead_process():
    """A process that has exited is reported dead, so the watchdog relaunches."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()  # the child has now exited
    # Tiny settle window: on some platforms the kernel finishes reaping the
    # handle a hair after wait() returns.
    time.sleep(0.1)
    assert watchdog._is_alive(p.pid) is False


# --- _read_pid ---------------------------------------------------------------

def test_read_pid_parses_valid_file(monkeypatch, tmp_path):
    pid_file = tmp_path / "wispr.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(watchdog, "PID_FILE", pid_file)
    assert watchdog._read_pid() == 12345


def test_read_pid_tolerates_whitespace(monkeypatch, tmp_path):
    pid_file = tmp_path / "wispr.pid"
    pid_file.write_text("  6789\n")
    monkeypatch.setattr(watchdog, "PID_FILE", pid_file)
    assert watchdog._read_pid() == 6789


def test_read_pid_returns_none_when_missing(monkeypatch, tmp_path):
    """No PID file → daemon never started or exited cleanly → don't relaunch."""
    monkeypatch.setattr(watchdog, "PID_FILE", tmp_path / "does_not_exist.pid")
    assert watchdog._read_pid() is None


def test_read_pid_returns_none_on_garbage(monkeypatch, tmp_path):
    """A corrupt PID file must not raise — it degrades to 'unknown' (None) so
    the poll loop simply skips this tick rather than crashing the watchdog."""
    pid_file = tmp_path / "wispr.pid"
    pid_file.write_text("not-a-number")
    monkeypatch.setattr(watchdog, "PID_FILE", pid_file)
    assert watchdog._read_pid() is None
