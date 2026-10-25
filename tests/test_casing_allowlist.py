"""Unit tests for src/casing_allowlist.py: the bundled proper-noun set the
casing flattener must never lowercase, and the words it must deliberately leave
out so ordinary prose still flattens."""
from __future__ import annotations

import pytest

from src import casing_allowlist as ca
from src.cleanup import _polish_text

pytestmark = pytest.mark.unit

# Sanity floor, not an exact count: catches a whole group being emptied by a
# bad edit without breaking every time someone adds a name.
MIN_ENTRIES = 250


def test_proper_nouns_is_an_immutable_set_of_lowercase_words():
    nouns = ca.proper_nouns()
    assert isinstance(nouns, frozenset)
    assert all(w == w.lower() for w in nouns)


def test_module_constant_is_the_precomputed_function_result():
    assert ca.PROPER_NOUNS == ca.proper_nouns()


def test_each_call_returns_an_equal_set():
    assert ca.proper_nouns() == ca.proper_nouns()


def test_constant_cannot_be_mutated_by_a_caller():
    with pytest.raises(AttributeError):
        ca.PROPER_NOUNS.add("anything")  # type: ignore[attr-defined]


def test_set_is_not_empty_or_truncated():
    assert len(ca.PROPER_NOUNS) >= MIN_ENTRIES


def test_every_entry_is_a_single_alphabetic_token():
    # The flattener looks words up one token at a time, so a multi-word or
    # punctuated entry could never match anything.
    bad = sorted(w for w in ca.PROPER_NOUNS if not (w.isalpha() and w.isascii()))
    assert bad == []


@pytest.mark.parametrize("word", [
    # tech companies, languages, tools
    "google", "github", "python", "javascript", "docker", "postgresql",
    "chatgpt", "openai", "tiktok", "aws",
    # countries and nationalities
    "canada", "germany", "nepal", "american", "japanese",
    # cities and the distinctive second word of multi-word places
    "london", "tokyo", "seattle", "york", "diego", "kong", "janeiro",
    # regions
    "europe", "asia", "carolina",
    # first names
    "sarah", "michael", "jessica", "elizabeth",
])
def test_known_proper_nouns_are_protected(word):
    assert word in ca.PROPER_NOUNS


@pytest.mark.parametrize("word", [
    # called out in the module docstring
    "mark", "will", "may", "bill", "rose", "grace", "reading",
    # called out beside the name and city lists
    "june", "april", "joy", "hope", "faith", "dawn", "summer", "crystal",
    "holly", "jasmine", "pearl", "nice", "mobile", "bath", "hull",
])
def test_words_that_double_as_ordinary_words_are_not_protected(word):
    assert word not in ca.PROPER_NOUNS


@pytest.mark.parametrize("word", ["", " ", "sarah ", "Sarah", "SARAH"])
def test_lookup_is_exact_on_the_lowercased_form(word):
    # Callers lowercase the token first; the set itself never matches a
    # capitalized, padded or empty spelling.
    assert word not in ca.PROPER_NOUNS


@pytest.mark.parametrize("raw,expected", [
    ("We met Sarah in London last week", "We met Sarah in London last week."),
    ("I deployed it on Docker with Python and Redis",
     "I deployed it on Docker with Python and Redis."),
    ("I think the Quarterly Report is fine", "I think the quarterly report is fine."),
    # excluded words flatten even though they are also names
    ("We met Mark and Grace in Reading", "We met mark and grace in reading."),
])
def test_flattener_keeps_bundled_nouns_and_flattens_the_rest(raw, expected):
    assert _polish_text(raw, ca.PROPER_NOUNS) == expected


def test_without_the_allowlist_the_same_names_would_be_flattened():
    # This is the failure the module exists to prevent.
    out = _polish_text("We met Sarah in London last week", frozenset())
    assert out == "We met sarah in london last week."
