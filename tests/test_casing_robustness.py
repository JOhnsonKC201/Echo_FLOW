# -*- coding: utf-8 -*-
"""Regression tests for the casing-robustness audit (workflow #3).

Each test pins a reproduced mishandling that was fixed in _polish_text /
_apply_learned_casing. Grouped by the audit's defect IDs (H1–H4, M1–M6).
"""
from __future__ import annotations

import pytest

from src.cleanup import _polish_text, Cleaner


class _FakeMiner:
    def __init__(self, canon):
        self._canon = canon

    def canonical_casings(self):
        return dict(self._canon)

    def confident_patterns(self, min_confidence=0.7):
        return {}


def _cleaner(canon):
    c = Cleaner({"enabled": True, "provider": "ollama"})
    c._pattern_miner = _FakeMiner(canon)
    return c


# H1 — sentence-cap must not corrupt lowercase-leading internal-caps brands.
@pytest.mark.parametrize("inp,exp", [
    ("iOS rocks", "iOS rocks."),
    ("mRNA helps", "mRNA helps."),
    ("macOS update ready", "macOS update ready."),
    ("iPhone15 launched", "iPhone15 launched."),
    ("we love iOS. mRNA wins", "We love iOS. mRNA wins."),
])
def test_h1_internal_caps_survive_sentence_cap(inp, exp):
    assert _polish_text(inp, protected=frozenset()) == exp


# H2 — comma-storm must not flatten legitimate ALLCAPS/acronym comma lists.
def test_h2_acronym_comma_list_keeps_commas():
    assert (_polish_text("SQL, iOS, GDPR, COVID, GPT", protected=frozenset())
            == "SQL, iOS, GDPR, COVID, GPT.")


# H3 — the storm's mid-lowercase pass must not split internal-caps brands.
def test_h3_storm_preserves_internal_caps():
    out = _polish_text("yes, no, TikTok, iPhone, ok, fine.",
                       protected=frozenset({"london", "texas"}))
    assert out == "Yes no TikTok iPhone ok fine."


def test_h3_finalize_canon_not_undone_by_storm():
    c = _cleaner({"tiktok": "TikTok"})
    out = c._finalize("tiktok, Tiktok, TIKTOK, TikTok.")
    assert "tikTok" not in out and "TikTok" in out


# H4 — abbreviations are not sentence boundaries.
@pytest.mark.parametrize("inp,exp", [
    ("We met in the U.S. and then went home.",
     "We met in the U.S. and then went home."),
    ("Use a flag, e.g. verbose, and run.", "Use a flag, e.g. verbose, and run."),
])
def test_h4_abbreviations_not_sentence_end(inp, exp):
    assert _polish_text(inp, protected=frozenset()) == exp


# M1 — curly apostrophe possessives normalize the same as ASCII.
def test_m1_curly_apostrophe_possessive_flatten():
    assert _polish_text("the Driver’S license", protected=frozenset()) == \
        "The driver’s license."
    assert _polish_text("I love iPhone’S design", protected=frozenset()) == \
        "I love iPhone’s design."


def test_m1_curly_apostrophe_possessive_canon():
    c = _cleaner({"tiktok": "TikTok"})
    assert c._apply_learned_casing("the tiktok’s feed") == "the TikTok’s feed"


# M2/M4 — sentence-initial through openers and leading apostrophe.
@pytest.mark.parametrize("inp,exp", [
    ("hello there. (this is great)", "Hello there. (This is great)"),
    ('done. "next one"', 'Done. "Next one"'),
    ("'twas the night before christmas", "'Twas the night before christmas."),
])
def test_m2_m4_sentence_start_through_openers(inp, exp):
    assert _polish_text(inp, protected=frozenset()) == exp


# M3 — unicode ellipsis is a sentence terminator.
def test_m3_unicode_ellipsis_terminator():
    assert _polish_text("really… Sarah left", protected=frozenset()) == \
        "Really… Sarah left."


# M5 — non-Latin capitalization + de-Title-Case are Unicode-aware.
def test_m5_non_latin_sentence_cap():
    assert _polish_text("étienne went home", protected=frozenset()) == \
        "Étienne went home."


def test_m5_non_latin_flatten():
    # Cyrillic: sentence-initial preserved/capitalized, mid-sentence flattened.
    assert _polish_text("Привет Мир",
                        protected=frozenset({"london"})) == \
        "Привет мир."


# M6 — honorifics survive the flattener; names after a title are capitalized.
def test_m6_honorifics_and_names():
    out = _polish_text("Dr. Smith, Mr. Jones, Ms. Lee, Mrs. Day.",
                       protected=frozenset({"london", "texas"}))
    assert out == "Dr. Smith, Mr. Jones, Ms. Lee, Mrs. Day."
