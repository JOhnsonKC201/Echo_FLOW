"""Phase 12 — trailing voice command detection + Enter injection."""
from __future__ import annotations

import pytest

from src.actions import detect_trailing_command


# --- Detection ---------------------------------------------------------------

@pytest.mark.parametrize("text,expected_stripped", [
    ("Send the email. Press enter.", "Send the email."),
    ("send the email press enter", "send the email"),
    ("Please send the message. Submit.", "Please send the message."),
    ("Ship it now. Send it.", "Ship it now."),
    ("Okay, send the message", "Okay,"),
    ("Hit enter when ready hit enter", "Hit enter when ready"),  # trailing match only
    ("Please please press enter", "Please"),  # leading "please" consumed by politeness group
    ("Reply approved please submit", "Reply approved"),
])
def test_detects_trailing_enter(text, expected_stripped):
    result = detect_trailing_command(text)
    assert result is not None, f"expected match for: {text}"
    cmd, stripped = result
    assert cmd == "enter"
    assert stripped == expected_stripped


@pytest.mark.parametrize("text", [
    "",
    None,
    "Just a normal sentence.",
    "I'll press enter when I'm done.",       # not at the end
    "Submit a PR tomorrow.",                  # not at the end
    "press enter",                            # bare command, no payload
    "send it",                                # bare command
    "ok",                                     # nothing to detect
])
def test_no_trailing_command(text):
    assert detect_trailing_command(text) is None


def test_detection_is_case_insensitive():
    r = detect_trailing_command("Send the report. PRESS ENTER!")
    assert r is not None and r[0] == "enter"


def test_detection_strips_trailing_punctuation():
    # Closing punctuation AFTER the command is consumed; payload-ending
    # punctuation BEFORE the command is preserved.
    r = detect_trailing_command('Reply now. Send it!!')
    assert r is not None
    cmd, stripped = r
    assert cmd == "enter"
    assert stripped == "Reply now."


# --- Injector.send_key (smoke; pyautogui may not be installed) ---------------

def test_send_key_returns_false_when_pyautogui_unavailable(monkeypatch):
    """If pyautogui isn't importable, send_key swallows and returns False."""
    import sys
    from src.inject import Injector
    # Force ImportError.
    monkeypatch.setitem(sys.modules, "pyautogui", None)
    inj = Injector()
    assert inj.send_key("enter") is False
