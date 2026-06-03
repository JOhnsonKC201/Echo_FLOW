"""Tests for adjacent repeated-phrase collapsing in the cleanup pipeline.

Covers the Whisper double-transcription artifact ("Open Browser Open Browser")
and — critically — the false-positive cases that MUST be left unchanged.
"""
from __future__ import annotations

import pytest

from src.cleanup import collapse_repeats, _polish_text


# (input, expected) — collapsed cases.
COLLAPSE_CASES = [
    ("Open Browser Open Browser", "Open Browser"),
    ("Not Opening In Chrome Not Opening In Chrome", "Not Opening In Chrome"),
    ("Open Browser Open Browser Open Browser", "Open Browser"),  # triple → one
    ("the the", "the"),       # classic stutter, always an artifact
    ("is is", "is"),
    ("hello hello world", "hello world"),
    # Mixed: phrase repeat inside a longer sentence.
    ("please Open Browser Open Browser now", "please Open Browser now"),
]

# (input, expected==input) — must be left UNCHANGED.
UNCHANGED_CASES = [
    "very very good",          # emphasis
    "no no no",                # emphasis, triple
    "ha ha ha",                # laughter
    "had had a feeling",       # grammatical past-perfect
    "that that is wrong",      # grammatical demonstrative
    "2 2 2",                   # numeric sequence
    "1 2 1 2",                 # numeric phrase
    "hello world",             # no repeat
    "I I",                     # single-char word (existing rule preserves)
    "",                        # empty contract
    "   ",                     # whitespace contract
]


@pytest.mark.parametrize("inp,expected", COLLAPSE_CASES)
def test_collapse_positive(inp, expected):
    assert collapse_repeats(inp) == expected


@pytest.mark.parametrize("inp", UNCHANGED_CASES)
def test_collapse_leaves_unchanged(inp):
    assert collapse_repeats(inp) == inp


def test_new_york_collapses_intentionally():
    # Accepted limitation, pinned so it's a deliberate choice, not a surprise:
    # "New York, New York" is a 2-gram repeat and collapses. If this ever needs
    # special-casing, change here on purpose.
    assert collapse_repeats("New York, New York") == "New York,"


def test_polish_text_collapses_repeat_no_protected():
    # _polish_text is the universal finalizer; the collapse must run there too.
    out = _polish_text("Open Browser Open Browser")
    assert out.lower().count("open browser") == 1


def test_polish_text_collapses_repeat_with_protected():
    out = _polish_text("Open Browser Open Browser", protected=frozenset())
    assert out.lower().count("open browser") == 1
