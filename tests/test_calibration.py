"""Voice calibration — session state, accuracy, and seeding (Phase 3)."""
from __future__ import annotations

from src.calibration import (
    CalibrationSession, word_accuracy, misheard_terms, apply_seeds,
)


# --- word_accuracy -----------------------------------------------------------

def test_word_accuracy_exact_is_one():
    assert word_accuracy("open the settings folder", "open the settings folder") == 1.0


def test_word_accuracy_partial():
    a = word_accuracy("deploy to Kubernetes today", "deploy to cube are net today")
    assert 0.0 < a < 1.0


def test_word_accuracy_empty_heard_is_zero():
    assert word_accuracy("anything here", "") == 0.0


# --- CalibrationSession state machine ----------------------------------------

def test_session_advances_and_completes():
    s = CalibrationSession(["one two", "three four"])
    assert s.active and not s.done
    assert s.progress()["current"] == "one two"

    assert s.submit("won too") == 1
    assert s.progress()["recorded"] == 1
    assert s.progress()["current"] == "three four"

    assert s.submit("three for") == 2
    assert s.done and not s.active
    assert s.submit("late") == -1              # nothing left to record


def test_session_pairs_and_baseline():
    s = CalibrationSession(["open the door", "close the window"])
    s.submit("open the door")                  # perfect
    s.submit("close the window")               # perfect
    pairs = s.pairs()
    assert len(pairs) == 2
    assert s.baseline_accuracy() == 1.0


# --- misheard_terms: ground-truth dictionary candidates ----------------------

def test_misheard_terms_flags_fumbled_proper_nouns():
    terms = misheard_terms("We use Kubernetes and FastAPI daily",
                           "we use cube are nets and fast a p i daily")
    assert "Kubernetes" in terms and "FastAPI" in terms


def test_misheard_terms_ignores_correctly_heard_and_plain_words():
    # "Kubernetes" heard fine → not flagged; plain words never flagged.
    terms = misheard_terms("deploy Kubernetes now", "deploy Kubernetes now")
    assert terms == []


# --- apply_seeds: records corrections + pins terms ---------------------------

def _miner(tmp_path):
    from src.learn import PatternMiner
    return PatternMiner(str(tmp_path / "p.db"))


def _history(tmp_path):
    from src.history import History
    return History(str(tmp_path / "h.db"))


def test_apply_seeds_pins_misheard_terms_and_records(tmp_path):
    from src.dashboard import vocabulary
    miner = _miner(tmp_path)
    h = _history(tmp_path)
    s = CalibrationSession(["We deployed Kubernetes on Tuesday"])
    s.submit("we deployed cube are nets on tuesday")

    summary = apply_seeds(s, miner, h.conn)

    assert summary["pairs"] == 1
    assert "Kubernetes" in summary["pinned_terms"]
    # Pinned directly into the dictionary (we have ground truth).
    assert "Kubernetes" in [t["term"] for t in vocabulary.list_terms(h.conn)]
    # And the (heard -> target) correction was recorded for the miner.
    assert summary["recorded"] >= 1


def test_apply_seeds_perfect_reading_pins_nothing(tmp_path):
    from src.dashboard import vocabulary
    miner = _miner(tmp_path)
    h = _history(tmp_path)
    s = CalibrationSession(["open the settings folder"])
    s.submit("open the settings folder")
    summary = apply_seeds(s, miner, h.conn)
    assert summary["pinned"] == 0
    assert vocabulary.list_terms(h.conn) == []
