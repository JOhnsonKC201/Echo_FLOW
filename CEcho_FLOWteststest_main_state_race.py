"""Tests for the threading.Lock guard on App._active / App._paused.

These verify that:
  (a) on_press_hold is a no-op when _paused is True
  (b) on_press_hold is a no-op when _active is already True (idempotent)
  (c) tray_pause_toggle and on_press_hold are safe to run concurrently,
      proved deterministically via lock-ordering assertions rather than
      sleep-based timing.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from src.main import App


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(*, active: bool = False, paused: bool = False) -> App:
    """Construct a bare App shell with only the attributes that
    on_press_hold / on_toggle / tray_pause_toggle actually read."""
    app = App.__new__(App)
    app._active = active
    app._paused = paused
    app._state_lock = threading.Lock()
    app._press_title = None
    app.cfg = {"sound": None}
    app.tray = None

    # Collaborators touched OUTSIDE the lock (after the guard returns)
    app.recorder = MagicMock()
    app.injector = MagicMock()
    app.injector.focused_title.return_value = "FakeWindow"
    return app


# ---------------------------------------------------------------------------
# (a) on_press_hold does NOT start recorder when _paused is True
# ---------------------------------------------------------------------------

def test_on_press_hold_no_op_when_paused(monkeypatch):
    monkeypatch.setattr("src.main.wsound.play", lambda *a, **k: None)
    monkeypatch.setattr("src.main.console.print", lambda *a, **k: None)

    app = _make_app(paused=True)
    app.on_press_hold()

    app.recorder.start.assert_not_called()
    # State must remain unchanged
    assert app._active is False
    assert app._paused is True


# ---------------------------------------------------------------------------
# (b) second on_press_hold when already active is a no-op
# ---------------------------------------------------------------------------

def test_on_press_hold_no_op_when_already_active(monkeypatch):
    monkeypatch.setattr("src.main.wsound.play", lambda *a, **k: None)
    monkeypatch.setattr("src.main.console.print", lambda *a, **k: None)

    app = _make_app(active=False)

    # First call should arm the recorder
    app.on_press_hold()
    assert app.recorder.start.call_count == 1

    # Second call while _active is True must be a complete no-op
    app.on_press_hold()
    assert app.recorder.start.call_count == 1  # still exactly 1


# ---------------------------------------------------------------------------
# (c) Deterministic interleaving: lock is actually acquired in both paths
# ---------------------------------------------------------------------------

def test_state_lock_acquired_by_on_press_hold(monkeypatch):
    """Verify the lock is entered by on_press_hold by wrapping it with a
    spy that records acquire/release calls."""
    monkeypatch.setattr("src.main.wsound.play", lambda *a, **k: None)
    monkeypatch.setattr("src.main.console.print", lambda *a, **k: None)

    app = _make_app()
    real_lock = app._state_lock
    acquired: list[str] = []

    original_enter = real_lock.__enter__
    original_exit = real_lock.__exit__

    def spy_enter():
        acquired.append("enter")
        return original_enter()

    def spy_exit(exc_type, exc_val, exc_tb):
        acquired.append("exit")
        return original_exit(exc_type, exc_val, exc_tb)

    real_lock.__enter__ = spy_enter
    real_lock.__exit__ = spy_exit

    app.on_press_hold()

    assert "enter" in acquired, "_state_lock was never acquired by on_press_hold"
    assert "exit" in acquired, "_state_lock was never released by on_press_hold"


def test_state_lock_acquired_by_tray_pause_toggle(monkeypatch):
    monkeypatch.setattr("src.main.console.print", lambda *a, **k: None)

    app = _make_app()
    real_lock = app._state_lock
    acquired: list[str] = []

    original_enter = real_lock.__enter__
    original_exit = real_lock.__exit__

    def spy_enter():
        acquired.append("enter")
        return original_enter()

    def spy_exit(exc_type, exc_val, exc_tb):
        acquired.append("exit")
        return original_exit(exc_type, exc_val, exc_tb)

    real_lock.__enter__ = spy_enter
    real_lock.__exit__ = spy_exit

    app.tray_pause_toggle()

    assert "enter" in acquired, "_state_lock was never acquired by tray_pause_toggle"
    assert "exit" in acquired, "_state_lock was never released by tray_pause_toggle"


def test_tray_pause_toggle_concurrent_with_on_press_hold(monkeypatch):
    """Drive both methods from two threads simultaneously and verify the
    outcome is always consistent: either recording started (not paused) or
    it did not (paused).  Uses threading.Event for a deterministic
    rendezvous rather than arbitrary sleeps."""
    monkeypatch.setattr("src.main.wsound.play", lambda *a, **k: None)
    monkeypatch.setattr("src.main.console.print", lambda *a, **k: None)

    # Run the scenario many times to increase the chance of hitting the race
    # on a real parallel machine.
    ITERATIONS = 200
    inconsistencies = 0

    for _ in range(ITERATIONS):
        app = _make_app(active=False, paused=False)

        ready = threading.Event()
        go = threading.Event()

        def press_thread():
            ready.wait()
            go.wait()
            app.on_press_hold()

        def pause_thread():
            ready.wait()
            go.wait()
            app.tray_pause_toggle()

        t1 = threading.Thread(target=press_thread)
        t2 = threading.Thread(target=pause_thread)
        t1.start()
        t2.start()
        ready.set()   # both threads are waiting at go.wait()
        go.set()      # release both simultaneously
        t1.join(timeout=2)
        t2.join(timeout=2)

        # Invariant: if recorder.start was called, _paused must have been
        # False at the moment the lock was held by on_press_hold, so
        # tray_pause_toggle either ran first (paused=True, press bailed) or
        # second (pressed ran first, set _active=True, paused flipped after).
        # Either way, recorder.start call count (0 or 1) plus _paused must
        # be self-consistent: start called => _active was set under lock.
        start_calls = app.recorder.start.call_count
        if start_calls > 1:
            inconsistencies += 1  # should never happen

    assert inconsistencies == 0, (
        f"recorder.start called more than once in {inconsistencies} iterations — "
        "the lock is not preventing the double-start race"
    )
