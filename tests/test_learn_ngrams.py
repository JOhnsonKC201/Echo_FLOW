"""Multi-word (n-gram) substitution learning — the accent-phrase path.

The 1↔1 miner can't express "note to vec" → "node2vec" (3 tokens collapse to
1). These tests cover the n-gram diff, the phonetic gate, the confidence store,
and end-to-end application through the Cleaner.
"""
from __future__ import annotations

from src.learn import _diff_ngram_pairs, PatternMiner


# --- _diff_ngram_pairs: capture genuine mishearings, reject rewrites ----------

def test_ngram_diff_captures_multiword_mishearing():
    pairs = _diff_ngram_pairs("we ran note to vec on it", "we ran node2vec on it")
    assert ("note to vec", "node2vec") in pairs


def test_ngram_diff_rejects_unrelated_rewrite():
    # A meaning-changing paraphrase does NOT sound alike → not learned.
    pairs = _diff_ngram_pairs(
        "the results caught 14 bugs", "the results show the process works")
    assert pairs == []


def test_ngram_diff_skips_pure_single_token_sub():
    # A 1↔1 sub is the token miner's job, not the n-gram miner's.
    assert _diff_ngram_pairs("hello jonson there", "hello Johnson there") == []


def test_ngram_diff_ignores_long_spans():
    # A 4+ word replacement is a rewrite, not a mishearing — capped out.
    raw = "one two three four five six"
    cleaned = "alpha beta gamma delta five six"
    assert _diff_ngram_pairs(raw, cleaned) == []


# --- PatternMiner: record + confident_ngrams --------------------------------

def test_record_populates_ngrams_even_with_no_1to1_pair(tmp_path):
    db = str(tmp_path / "p.db")
    miner = PatternMiner(db)
    # No 1↔1 sub here — only the multi-word one. It must still be recorded.
    n = miner.record("run note to vec now", "run node2vec now")
    assert n >= 1
    miner.record("try note to vec again", "try node2vec again")
    ngrams = miner.confident_ngrams(min_confidence=0.75, min_total=2)
    assert ngrams.get("note to vec") == "node2vec"


def test_confident_ngrams_respects_min_total(tmp_path):
    db = str(tmp_path / "p.db")
    miner = PatternMiner(db)
    miner.record("run note to vec now", "run node2vec now")   # seen once
    # min_total=2 → a single observation is below the bar.
    assert miner.confident_ngrams(min_confidence=0.75, min_total=2) == {}
    assert miner.confident_ngrams(min_confidence=0.75, min_total=1).get(
        "note to vec") == "node2vec"


def test_confident_ngrams_drops_low_confidence(tmp_path):
    db = str(tmp_path / "p.db")
    miner = PatternMiner(db)
    # Same trigger heard two different ways → each 0.5 confidence, below 0.75.
    miner.record("run fast a p i now", "run FastAPI now")
    miner.record("run fast a p i now", "run Fast API now")
    assert miner.confident_ngrams(min_confidence=0.75, min_total=2) == {}


# --- End-to-end through the Cleaner apply seam -------------------------------

def _cleaner_with_miner(miner):
    from src.cleanup import Cleaner
    c = Cleaner({"enabled": True, "provider": "learned",
                 "learned": {"min_ngram_confidence": 0.75, "min_ngram_total": 2}})
    c._pattern_miner = miner
    return c


def test_apply_learned_ngrams_rewrites_the_phrase(tmp_path):
    db = str(tmp_path / "p.db")
    miner = PatternMiner(db)
    miner.record("run note to vec now", "run node2vec now")
    miner.record("try note to vec again", "try node2vec again")
    c = _cleaner_with_miner(miner)
    assert c._apply_learned_ngrams("please run note to vec today") == \
        "please run node2vec today"


def test_apply_learned_ngrams_noop_without_confident_phrase(tmp_path):
    db = str(tmp_path / "p.db")
    miner = PatternMiner(db)
    c = _cleaner_with_miner(miner)
    text = "nothing to change here"
    assert c._apply_learned_ngrams(text) == text
