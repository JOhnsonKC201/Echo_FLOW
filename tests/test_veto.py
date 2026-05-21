"""Tests for HotkeyListener veto behavior.

These directly drive _on_press / _on_release to simulate keyboard events.
"""
from __future__ import annotations

from pynput import keyboard

from src.hotkey import HotkeyListener


def test_listener_fires_when_veto_not_present():
    fires = []
    h = HotkeyListener("ctrl+shift", "hold", lambda: fires.append("on"),
                       lambda: fires.append("off"))
    h._on_press(keyboard.Key.ctrl)
    h._on_press(keyboard.Key.shift)
    assert fires == ["on"]
    h._on_release(keyboard.Key.shift)
    assert fires == ["on", "off"]


def test_listener_does_not_fire_when_veto_already_held():
    """Pressing Win first, then Ctrl+Shift, must NOT fire dictation."""
    fires = []
    vetoes = []
    h = HotkeyListener(
        "ctrl+shift", "hold",
        on_activate=lambda: fires.append("on"),
        on_deactivate=lambda: fires.append("off"),
        veto_keys="win",
        on_veto=lambda: vetoes.append("veto"),
    )
    h._on_press(keyboard.Key.cmd)   # Win pressed first
    h._on_press(keyboard.Key.ctrl)
    h._on_press(keyboard.Key.shift)
    assert fires == []   # veto key held → dictation never fires
    assert vetoes == []  # no veto callback fired (wasn't active to begin with)


def test_listener_cancels_when_veto_added_during_active():
    """Press Ctrl+Shift (recording starts) → press Win → recording cancels."""
    fires = []
    vetoes = []
    h = HotkeyListener(
        "ctrl+shift", "hold",
        on_activate=lambda: fires.append("on"),
        on_deactivate=lambda: fires.append("off"),
        veto_keys="win",
        on_veto=lambda: vetoes.append("veto"),
    )
    h._on_press(keyboard.Key.ctrl)
    h._on_press(keyboard.Key.shift)
    assert fires == ["on"]              # dictation started
    h._on_press(keyboard.Key.cmd)       # Win adds → veto fires
    assert vetoes == ["veto"]
    # No "off" yet (release path didn't run); but listener is no longer active.
    assert h._active is False


def test_listener_release_after_veto_does_not_fire_deactivate():
    """After veto cancels, releasing keys shouldn't fire the deactivate hook."""
    deactivations = []
    h = HotkeyListener(
        "ctrl+shift", "hold",
        on_activate=lambda: None,
        on_deactivate=lambda: deactivations.append("d"),
        veto_keys="win",
        on_veto=lambda: None,
    )
    h._on_press(keyboard.Key.ctrl)
    h._on_press(keyboard.Key.shift)
    h._on_press(keyboard.Key.cmd)       # veto → _active=False
    h._on_release(keyboard.Key.cmd)
    h._on_release(keyboard.Key.shift)
    h._on_release(keyboard.Key.ctrl)
    assert deactivations == []          # release shouldn't fire deactivate


def test_three_key_listener_fires_normally():
    """The re-paste listener ({Ctrl, Shift, Win}) should still fire on the full combo."""
    fires = []
    h = HotkeyListener("ctrl+shift+win", "toggle", lambda: fires.append("paste"))
    h._on_press(keyboard.Key.ctrl)
    h._on_press(keyboard.Key.shift)
    h._on_press(keyboard.Key.cmd)
    assert fires == ["paste"]
