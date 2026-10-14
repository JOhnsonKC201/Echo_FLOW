"""Spoken formatting commands and the "scratch that" backtrack.

These run before any model and also when there is no model, so the tests lean
on the half that matters most: ordinary sentences that happen to contain the
words "period", "comma", "new line" must come through untouched. Converting a
word the user meant as a word is data loss they cannot see; leaving a spoken
"comma" in is a blemish they can fix.
"""
import pytest

from src import spoken_format


# --- line and paragraph breaks ----------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("first line new line second line",            "first line\nsecond line"),
    ("first line next line second line",           "first line\nsecond line"),
    ("para one new paragraph para two",            "para one\n\npara two"),
    # Whisper usually punctuates the command as if it were a sentence.
    ("First line. New line. Second line.",         "First line.\nSecond line."),
    ("First line, new line, second line.",         "First line\nsecond line."),
    ("Dear Sam, new paragraph Thanks for the file.", "Dear Sam,\n\nThanks for the file."),
    ("end of the note new line",                   "end of the note\n"),
])
def test_breaks(raw, expected):
    out, applied = spoken_format.apply(raw)
    assert out == expected
    assert applied


# --- punctuation ------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("hello comma world",                    "hello, world"),
    ("is it done question mark",             "is it done?"),
    ("ship it exclamation mark",             "ship it!"),
    ("ship it exclamation point",            "ship it!"),
    ("ship it full stop",                    "ship it."),
    ("two items colon a and b",              "two items: a and b"),
    ("one thing semicolon another",          "one thing; another"),
    ("ship it period",                       "ship it."),
    ("ship it period Then we rest",          "ship it. Then we rest"),
    # Whisper already wrote the mark it heard; never double it.
    ("Ship it, period.",                     "Ship it."),
    ("hello, comma, world",                  "hello, world"),
    ("Is it done? Question mark.",           "Is it done?"),
    ("Ship it. Full stop.",                  "Ship it."),
    # Case from Whisper capitalizing a command it treated as a sentence.
    ("Hello Comma world",                    "Hello, world"),
])
def test_punctuation(raw, expected):
    out, applied = spoken_format.apply(raw)
    assert out == expected
    assert applied


# --- backtrack --------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("meet at five scratch that meet at six",        "meet at six"),
    ("meet at five strike that meet at six",         "meet at six"),
    ("Meet at five. Scratch that. Meet at six.",     "Meet at six."),
    # Only the current sentence goes; earlier sentences stay.
    ("Thanks for the file. Meet at five scratch that meet at six.",
     "Thanks for the file. Meet at six."),
    # The break is a boundary too: the previous line survives.
    ("first line\nsecond draft scratch that second line",
     "first line\nsecond line"),
    # Nothing after the command: the clause is simply gone.
    ("Thanks for the file. Meet at five, scratch that.",
     "Thanks for the file."),
])
def test_scratch_that(raw, expected):
    out, applied = spoken_format.apply(raw)
    assert out == expected
    assert applied


def test_scratch_that_never_empties_the_text():
    out, applied = spoken_format.apply("scratch that")
    assert out == "scratch that"
    assert applied == []


def test_scratch_that_runs_before_the_other_commands():
    # The struck clause contained a command; it must not fire.
    out, _ = spoken_format.apply("send it comma scratch that keep it")
    assert out == "keep it"


# --- words that must survive as words ----------------------------------------

@pytest.mark.parametrize("raw", [
    "the trial period ended last week",
    "a period of two weeks",
    "a comma splice is still a mistake",
    "the semicolon key is next to L",
    "add a new line to the file",
    "the new paragraph looks good",
    "every new line in the log is a request",
    "I struck that idea from the list",
    "no question marks in headlines",
])
def test_ordinary_words_untouched(raw):
    out, applied = spoken_format.apply(raw)
    assert out == raw
    assert applied == []


def test_empty_and_whitespace_passthrough():
    assert spoken_format.apply("") == ("", [])
    assert spoken_format.apply("   ") == ("   ", [])


def test_reports_what_it_applied():
    out, applied = spoken_format.apply("x comma b new line c. y scratch that d")
    assert out == "x, b\nc. D"
    assert applied == ["scratch that", "new line", "comma"]
