"""Deterministic delete-first pass (src/deadweight.py).

Cuts sentences that do no work — topic-announcement openers, empty-optimism
closers, pure throat-clearing — but protects anything carrying a fact, never
empties a paragraph, and never touches a lone sentence.
"""
from __future__ import annotations

from src.deadweight import trim


def test_cuts_opener_hedge_and_closer():
    text = ("Machine learning has fundamentally transformed the landscape of PPI "
            "prediction. Deep models beat classical features on the benchmarks. "
            "However, it is important to note that these approaches have limits. "
            "Class imbalance remains hard. Nevertheless, the field continues to "
            "evolve rapidly.")
    kept, cuts = trim(text)
    assert len(cuts) == 3
    assert "transformed the landscape" in cuts[0]
    assert "important to note" in cuts[1]
    assert "continues to" in cuts[2]
    # The two contentful sentences survive.
    assert "beat classical features" in kept and "Class imbalance" in kept


def test_protects_sentences_with_numbers():
    text = ("It is important to note that we caught 14 regressions. "
            "The rollout reached the team.")
    kept, cuts = trim(text)
    assert cuts == []                       # the "14" sentence is protected
    assert "14 regressions" in kept


def test_protects_sentences_with_proper_nouns():
    text = ("Raft, introduced by Ongaro and Ousterhout, is a consensus protocol. "
            "It is worth noting that consensus is hard.")
    kept, cuts = trim(text)
    assert "Raft" in kept
    assert cuts == ["It is worth noting that consensus is hard."]


def test_never_empties_a_paragraph():
    # A paragraph made only of dead sentences keeps its longest one.
    text = ("In today's world of AI, everything has changed. "
            "The possibilities are endless.")
    kept, cuts = trim(text)
    assert kept.strip() != ""
    assert len(cuts) == 1                   # one cut, one kept


def test_never_cuts_a_lone_sentence():
    assert trim("It is important to note that this is the only sentence.")[1] == []


def test_preserves_paragraph_breaks():
    text = ("It is important to note that para one has a hedge. Real content here "
            "about the parser.\n\nReal content in para two about scans. "
            "Nevertheless, the future is bright.")
    kept, cuts = trim(text)
    assert "\n\n" in kept                   # paragraph structure preserved
    assert len(cuts) == 2                   # the hedge and the optimism closer


def test_clean_text_is_untouched():
    text = ("We shipped the parser on Tuesday. It reads 12 formats and fails on "
            "rotated scans.")
    kept, cuts = trim(text)
    assert cuts == [] and kept == text


def test_handles_empty():
    assert trim("") == ("", [])
    assert trim(None) == ("", [])           # type: ignore[arg-type]
