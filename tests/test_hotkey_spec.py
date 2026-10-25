"""Unit tests for src/hotkey_spec.py, the one grammar shared by the transform
validator and the pynput registration path."""
from __future__ import annotations

import pytest

from src import hotkey_spec as hs

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("combo,mods,key", [
    ("ctrl+shift", {"ctrl", "shift"}, None),
    ("Ctrl + Shift + P", {"ctrl", "shift"}, "p"),
    ("control+alt+1", {"ctrl", "alt"}, "1"),
    ("command+option+p", {"cmd", "alt"}, "p"),
    ("win+space", {"cmd"}, "space"),
    ("ctrl+f24", {"ctrl"}, "f24"),
    ("f9", set(), "f9"),
])
def test_parse_accepts_valid_chords(combo, mods, key):
    got_mods, got_key = hs.parse(combo)
    assert got_mods == frozenset(mods)
    assert got_key == key


@pytest.mark.parametrize("combo,fragment", [
    ("", "empty"),
    ("   ", "empty"),
    ("ctrl++p", "blank part"),
    ("ctrl+ctrl+p", "repeats"),
    ("control+ctrl+p", "repeats"),
    ("p+ctrl", "has to come last"),
    ("ctrl+f25", "stop at f24"),
    ("ctrl+f0", "stop at f24"),
    ("ctrl+backspace", "does not recognize"),
    ("ctrl", "second modifier"),
    ("p", "at least one modifier"),
    ("ctrl+!", "does not recognize"),
])
def test_parse_rejects_with_a_message_that_names_the_fix(combo, fragment):
    with pytest.raises(ValueError) as e:
        hs.parse(combo)
    assert fragment in str(e.value)


@pytest.mark.parametrize("combo", ["shift+ctrl+p", "Ctrl + Shift + P", "control+shift+p"])
def test_canonical_orders_modifiers_so_one_chord_has_one_spelling(combo):
    assert hs.canonical(combo) == "ctrl+shift+p"


def test_canonical_modifier_only_chord():
    assert hs.canonical("shift+cmd+alt+ctrl") == "ctrl+alt+shift+cmd"


@pytest.mark.parametrize("combo,expected", [
    ("ctrl+alt+p", "<ctrl>+<alt>+p"),
    ("ctrl+shift", "<ctrl>+<shift>"),
    ("ctrl+space", "<ctrl>+<space>"),
    ("alt+f12", "<alt>+<f12>"),
    ("command+option+p", "<alt>+<cmd>+p"),
])
def test_to_pynput_brackets_named_keys_only(combo, expected):
    assert hs.to_pynput(combo) == expected


def test_to_pynput_raises_rather_than_returning_none():
    with pytest.raises(ValueError):
        hs.to_pynput("ctrl")


@pytest.mark.parametrize("combo,expected", [
    ("<ctrl>+<cmd>", "ctrl+cmd"),
    ("<shift>+<ctrl>+k", "ctrl+shift+k"),
    ("", None),
    (None, None),
    ("<ctrl>", None),
    ("nonsense+++", None),
])
def test_canonical_or_none_never_raises(combo, expected):
    assert hs.canonical_or_none(combo) == expected


@pytest.mark.parametrize("combo", [
    "ctrl+shift", "ctrl+alt+p", "command+option+p", "win+space", "f9",
    "ctrl+f24", "alt+shift+enter", "ctrl+tab", "ctrl+esc",
])
def test_everything_that_parses_registers_with_pynput(combo):
    """The drift this module exists to prevent: valid here, dead in pynput."""
    keyboard = pytest.importorskip("pynput.keyboard")
    keyboard.HotKey.parse(hs.to_pynput(combo))
