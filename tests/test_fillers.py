"""Deterministic filler removal.

The point of this module is that it runs for users with no model at all, so
these tests lean hard on the half that matters most: what it must NOT touch. A
filler left in is a blemish; a word eaten is data loss the user cannot see.
"""
import pytest

from src import fillers


# --- things that must be removed -------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("Um, I think we should ship it.",       "I think we should ship it."),
    ("Umm, hold on.",                        "Hold on."),
    ("Er, can you check the logs?",          "Can you check the logs?"),
    ("I uh went to the uh store.",           "I went to the store."),
    ("So it was, you know, broken.",         "So it was, broken."),
    ("Basically, the suite is green.",       "The suite is green."),
    ("It was, like, completely fine.",       "It was completely fine."),
])
def test_removes_filler(raw, expected):
    out, dropped = fillers.strip(raw)
    assert out == expected
    assert dropped


# --- things that must survive untouched ------------------------------------

@pytest.mark.parametrize("raw", [
    # "like" is a verb, a preposition, and a comparator far more often than a
    # filler. Only the comma-fenced form is filler.
    "I like it a lot.",
    "This looks like rain.",
    "Use a tool like ripgrep.",
    "I want a tool, like ripgrep, that is fast.",
    # Markers carrying real meaning outside a fenced position.
    "What kind of file is that?",
    "Sort of by date, please.",
    "He actually shipped it.",
    "The basically identical file.",
    "I mean it.",
    # Real words that START with a hesitation sound. These are the regression:
    # a hesitation pattern fenced only on its left turns Ahmed into Med and
    # Erlang into Lang.
    "Ahmed opened the PR.",
    "Umbrella term for the module.",
    "Erlang is on the list.",
    "Uber is hiring.",
    "The error is on line ten.",
    "Go ahead and merge.",
    "Her PR is ready.",
    "Ermine is a kind of stoat.",
])
def test_leaves_real_words_alone(raw):
    out, dropped = fillers.strip(raw)
    assert out == raw
    assert dropped == []


def test_never_empties_a_sentence():
    """Someone who dictated only a hesitation gets it back, not an empty paste."""
    for raw in ("um", "Hmm.", "uh uh"):
        out, dropped = fillers.strip(raw)
        assert out == raw
        # And the drop log rolls back with the text, so the UI cannot claim to
        # have removed something that is still on screen.
        assert dropped == []


def test_keeps_list_separators():
    """A fenced marker between list items must not eat the list's comma."""
    out, _ = fillers.strip("We tested A, you know, B, and C.")
    assert out == "We tested A, B, and C."


def test_is_purely_subtractive():
    """No path may introduce a word that was not dictated.

    This is the contract that lets the no-model path keep calling itself "your
    raw words": we delete, we never substitute.
    """
    raw = "Um, so I basically, you know, shipped the thing, like, yesterday."
    out, _ = fillers.strip(raw)
    raw_words = {w.strip(".,!?").lower() for w in raw.split()}
    out_words = {w.strip(".,!?").lower() for w in out.split()}
    assert out_words <= raw_words


def test_empty_and_whitespace():
    for raw in ("", "   ", "\n"):
        out, dropped = fillers.strip(raw)
        assert out == raw
        assert dropped == []


def test_multi_sentence():
    out, dropped = fillers.strip("Um, first point. Uh, second point.")
    assert out == "First point. Second point."
    assert len(dropped) == 2


def test_lowercase_input_stays_lowercase():
    """Recapitalization only restores what the input already had."""
    out, _ = fillers.strip("um, just a note")
    assert out == "just a note"
