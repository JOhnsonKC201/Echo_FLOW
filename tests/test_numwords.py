"""Number canonicalization — notation differences are not recognition errors."""
from __future__ import annotations

import pytest

from src.numwords import normalize, same_number


# --- the pairs that motivated the module ------------------------------------
# Left is how a read-aloud target is written; right is how Whisper writes it.

@pytest.mark.parametrize("spoken, written", [
    ("March fourth at nine thirty", "March 4th at 9.30"),
    ("March fourth at nine thirty", "March 4th at 9:30"),
    ("by twelve percent", "by 12%"),
    ("eighteen point five million dollars", "18.5 Million Dollars"),
    ("the first item", "the 1st item"),
    ("chapter twenty", "chapter 20"),
])
def test_spoken_and_written_numbers_agree(spoken, written):
    assert normalize(spoken) == normalize(written)


@pytest.mark.parametrize("a, b", [
    ("for", "four"),          # homophone, not the same number
    ("to", "two"),            # the mishearing calibration exists to catch
    ("Q3", "Qt11 you"),       # a genuine fumble
    ("nine", "ninety"),
])
def test_different_numbers_stay_different(a, b):
    assert normalize(a) != normalize(b)


# --- same_number: the filter that keeps the miner from learning notation -----

def test_same_number_drops_ordinal_notation():
    assert same_number("4th", "fourth")
    assert same_number("9.30", "nine thirty")


def test_same_number_keeps_real_mishearings():
    assert not same_number("From", "Turn")
    assert not same_number("to", "two")


def test_same_number_requires_an_actual_number():
    # Two equal non-numeric strings are not a notation difference.
    assert not same_number("module", "module")
    assert not same_number("", "")


# --- specifics ---------------------------------------------------------------

def test_percent_sign_becomes_a_word():
    assert normalize("12%") == ["12", "percent"]


def test_leading_zeros_are_stripped():
    assert normalize("05") == ["5"]


def test_point_only_dropped_between_numbers():
    assert normalize("eighteen point five") == ["18", "5"]
    # "point" as an ordinary noun must survive, or a real mishearing hides.
    assert "point" in normalize("the point of the meeting")


def test_scale_words_are_left_alone():
    # "million" appears verbatim on both sides, so it needs no conversion.
    assert normalize("18.5 million") == ["18", "5", "million"]


def test_documented_limits_fail_safely():
    # These compare unequal rather than compare wrongly — see the module
    # docstring. None occurs in CALIBRATION_SENTENCES.
    assert normalize("one hundred") != normalize("100")
    assert normalize("twenty-first") != normalize("21st")
