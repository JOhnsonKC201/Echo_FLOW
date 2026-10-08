"""Echo Flow's own Ctrl+V must not drive its own hotkey.

Seen in the log on 2026-09-30 23:42:34: a re-paste, then "hotkey pressed: REC
start" and "captured 0 samples" in the same second with nobody touching the
keyboard. The paste's synthetic Ctrl arrived while Shift was still held."""
from __future__ import annotations

from pynput import keyboard

from src import hotkey
from src.hotkey import HotkeyListener, own_keystrokes

CTRL, SHIFT = keyboard.Key.ctrl, keyboard.Key.shift


def _listener():
    events = []
    lis = HotkeyListener("ctrl+shift", mode="hold",
                         on_activate=lambda: events.append("start"),
                         on_deactivate=lambda: events.append("stop"))
    return lis, events


def test_our_own_ctrl_does_not_complete_the_hotkey():
    lis, events = _listener()
    lis._on_press(SHIFT)                      # user still holds Shift
    with own_keystrokes():
        lis._on_press(CTRL, True)             # the paste's Ctrl down
        lis._on_release(CTRL, True)
    assert events == []


def test_our_own_ctrl_up_does_not_end_a_live_recording():
    lis, events = _listener()
    lis._on_press(CTRL)
    lis._on_press(SHIFT)                      # user is dictating the next one
    with own_keystrokes():
        lis._on_press(CTRL, True)
        lis._on_release(CTRL, True)           # previous dictation pastes
    assert events == ["start"]
    lis._on_release(CTRL)                     # the real release still ends it
    assert events == ["start", "stop"]


def test_late_delivery_just_after_sending_is_still_ours(monkeypatch):
    lis, events = _listener()
    lis._on_press(SHIFT)
    with own_keystrokes():
        pass
    lis._on_press(CTRL, True)                 # the hook delivers after we return
    assert events == []


def test_injected_keys_from_other_programs_still_work(monkeypatch):
    """A remapper or remote desktop injects the hotkey for the user."""
    monkeypatch.setattr(hotkey, "_own_keys_until", 0.0)
    lis, events = _listener()
    lis._on_press(CTRL, True)
    lis._on_press(SHIFT, True)
    lis._on_release(SHIFT, True)
    assert events == ["start", "stop"]


def test_physical_keys_are_never_ignored_while_we_send():
    lis, events = _listener()
    with own_keystrokes():
        lis._on_press(CTRL, False)
        lis._on_press(SHIFT, False)
    assert events == ["start"]

