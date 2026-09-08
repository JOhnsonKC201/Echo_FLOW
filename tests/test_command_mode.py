"""Phase 13 — Command Mode classifier + safe-key allowlist."""
from __future__ import annotations

import pytest

from src import commands as cm


# --- Prefix stripping --------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("computer, select all", "select all"),
    ("Computer scroll down", "scroll down"),
    ("computer: copy that", "copy that"),
    ("COMPUTER, undo", "undo"),
    ("  computer save", "save"),
])
def test_strip_prefix_matches(text, expected):
    assert cm.strip_prefix(text, "computer") == expected


@pytest.mark.parametrize("text", [
    "select all the rows",          # no prefix
    "I told my computer to scroll", # prefix not at start
    "",
    "computer",                     # prefix only, no separator
])
def test_strip_prefix_misses(text):
    assert cm.strip_prefix(text, "computer") is None


def test_strip_prefix_custom_word():
    assert cm.strip_prefix("hey echo, undo", "hey echo") == "undo"


# --- Classifier --------------------------------------------------------------

@pytest.mark.parametrize("body,exp_type,exp_value", [
    ("select all",      "hotkey", "ctrl+a"),
    ("copy",            "hotkey", "ctrl+c"),
    ("copy that",       "hotkey", "ctrl+c"),
    ("paste it",        "hotkey", "ctrl+v"),
    ("undo",            "hotkey", "ctrl+z"),
    ("redo",            "hotkey", "ctrl+y"),
    ("save",            "hotkey", "ctrl+s"),
    ("find",            "hotkey", "ctrl+f"),
    ("close tab",       "hotkey", "ctrl+w"),
    ("reopen tab",      "hotkey", "ctrl+shift+t"),
    ("new tab",         "hotkey", "ctrl+t"),
    ("go back",         "hotkey", "alt+left"),
    ("go forward",      "hotkey", "alt+right"),
    ("go to top",       "hotkey", "ctrl+home"),
    ("go to bottom",    "hotkey", "ctrl+end"),
    ("scroll down",     "key",    "pagedown"),
    ("scroll up",       "key",    "pageup"),
    ("page down",       "key",    "pagedown"),
    ("press enter",     "key",    "enter"),
    ("escape",          "key",    "escape"),
    ("press tab",       "key",    "tab"),
    ("backspace",       "key",    "backspace"),
])
def test_classify_known_commands(body, exp_type, exp_value):
    r = cm.classify(body, platform="win32")
    assert r is not None, f"expected match: {body}"
    action_type, action_value, _label = r
    assert (action_type, action_value) == (exp_type, exp_value)


# --- Classifier on a Mac: same words, the Mac's keys -----------------------------

@pytest.mark.parametrize("body,exp_value", [
    ("select all",   "command+a"),
    ("copy that",    "command+c"),
    ("cut",          "command+x"),
    ("paste it",     "command+v"),
    ("undo",         "command+z"),
    ("redo",         "command+shift+z"),
    ("save",         "command+s"),
    ("find",         "command+f"),
    ("new tab",      "command+t"),
    ("close tab",    "command+w"),
    ("reopen tab",   "command+shift+t"),
    ("go to top",    "command+up"),
    ("go to bottom", "command+down"),
    ("go back",      "command+left"),
    ("go forward",   "command+right"),
])
def test_classify_returns_mac_chords_on_darwin(body, exp_value):
    r = cm.classify(body, platform="darwin")
    assert r is not None, f"expected match: {body}"
    assert (r[0], r[1]) == ("hotkey", exp_value)


@pytest.mark.parametrize("body", ["press enter", "escape", "scroll down", "backspace"])
def test_classify_single_keys_are_the_same_on_darwin(body):
    assert cm.classify(body, platform="darwin") == cm.classify(body, platform="win32")


def test_every_table_chord_is_still_allowed_on_darwin():
    # A chord the allowlist refused after translation would make the command
    # vanish silently on a Mac, which is exactly the failure this guards.
    from src import hostos
    for _pat, action_type, value, label in cm._COMMANDS:
        if action_type != "hotkey":
            continue
        assert cm.is_safe_hotkey(hostos.shortcut(value, "darwin")), label


def test_classify_defaults_to_the_running_platform():
    import sys
    assert cm.classify("undo") == cm.classify("undo", platform=sys.platform)


@pytest.mark.parametrize("body", [
    "",
    "rm -rf the world",
    "shutdown the computer",
    "open notepad",
    "type my password",          # no `type` action exists by design
    "delete all files",          # not on the allowlist
    "execute command",
])
def test_classify_unknown_commands_return_none(body):
    assert cm.classify(body) is None


# --- Safe-hotkey gate --------------------------------------------------------

@pytest.mark.parametrize("combo,expected", [
    ("ctrl+c", True),
    ("ctrl+shift+t", True),
    ("alt+left", True),
    ("ctrl+home", True),
    ("command+shift+z", True),  # the Mac chords must pass the same gate
    ("command+up", True),
    ("option+left", True),
    ("ctrl", False),            # modifier alone
    ("", False),
    ("ctrl+rm", False),         # unknown key
    ("foo+bar", False),         # unknown modifier
    ("ctrl++", False),          # malformed
])
def test_is_safe_hotkey(combo, expected):
    assert cm.is_safe_hotkey(combo) is expected


# --- Supported list ---------------------------------------------------------

def test_list_supported_non_empty_and_unique():
    labels = cm.list_supported()
    assert len(labels) > 10
    assert len(labels) == len(set(labels))


# --- End-to-end (prefix + classify) -----------------------------------------

def test_e2e_prefix_then_classify():
    body = cm.strip_prefix("Computer, scroll down please", "computer")
    assert body is not None
    r = cm.classify(body)
    assert r and r[0] == "key" and r[1] == "pagedown"


def test_e2e_unprefixed_command_text_is_not_classified():
    # Without going through strip_prefix, classify on a sentence with
    # command words elsewhere should not match — the patterns are anchored
    # to the start of the body.
    assert cm.classify("I want you to please select all the things") is None


# --- Injector.send_hotkey smoke (no pyautogui = False, never raises) --------

def test_send_hotkey_safe_without_pyautogui(monkeypatch):
    import sys
    from src.inject import Injector
    monkeypatch.setitem(sys.modules, "pyautogui", None)
    inj = Injector()
    assert inj.send_hotkey("ctrl+c") is False
    assert inj.send_hotkey("") is False


# --- Prefix validation (Commands page + Settings share this) -----------------
import pytest
from src import commands as _c


@pytest.mark.parametrize("good", ["computer", "jarvis", "friday", "echo", "Computer"])
def test_validate_prefix_accepts_good(good):
    assert _c.validate_prefix(good) is None


@pytest.mark.parametrize("bad", ["", "ab", "the", "yes", "j4rvis", "hey echo", "  ", "go!"])
def test_validate_prefix_rejects_bad(bad):
    assert _c.validate_prefix(bad) is not None
