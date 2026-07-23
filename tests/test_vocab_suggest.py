"""Low-confidence → dictionary-suggestion pipeline (Phase 1b)."""
from __future__ import annotations

from src.vocab_suggest import filter_candidates, _looks_like_term


# --- _looks_like_term: dictionary material vs ordinary words ------------------

def test_looks_like_term_accepts_names_and_tech_tokens():
    assert _looks_like_term("Kubernetes")     # proper noun
    assert _looks_like_term("FastAPI")         # internal caps
    assert _looks_like_term("node2vec")        # digit
    assert _looks_like_term("GPT4")


def test_looks_like_term_rejects_plain_words():
    assert not _looks_like_term("through")     # common lowercase word
    assert not _looks_like_term("the")
    assert not _looks_like_term("a")


# --- filter_candidates --------------------------------------------------------

def test_filter_keeps_unknown_term_present_in_output():
    low = [("Kubernetes", 0.4)]
    out = filter_candidates(low, "we deployed to Kubernetes today", set())
    assert out == [("Kubernetes", 0.4)]


def test_filter_drops_already_known_term():
    low = [("Kubernetes", 0.4)]
    out = filter_candidates(low, "we deployed to Kubernetes", {"kubernetes"})
    assert out == []


def test_filter_drops_word_absent_from_final_text():
    # A low-confidence word the cleanup discarded is not a term to pin.
    low = [("Kubernetes", 0.4)]
    out = filter_candidates(low, "we deployed the app", set())
    assert out == []


def test_filter_drops_plain_lowercase_word():
    low = [("through", 0.3)]
    out = filter_candidates(low, "we went through it", set())
    assert out == []


def test_filter_keeps_lowest_prob_per_term():
    low = [("Kubernetes", 0.5), ("Kubernetes", 0.2), ("Kubernetes", 0.45)]
    out = filter_candidates(low, "Kubernetes Kubernetes Kubernetes", set())
    assert out == [("Kubernetes", 0.2)]


# --- history.record_vocab_suggestion -----------------------------------------

def _history(tmp_path):
    from src.history import History
    return History(str(tmp_path / "h.db"))


def test_record_suggestion_counts_and_averages(tmp_path):
    h = _history(tmp_path)
    h.record_vocab_suggestion("Kubernetes", 0.4)
    h.record_vocab_suggestion("Kubernetes", 0.6)
    row = h.conn.execute(
        "SELECT term, count, avg_prob FROM vocab_suggestions WHERE term_lc='kubernetes'"
    ).fetchone()
    assert row[0] == "Kubernetes"
    assert row[1] == 2
    assert abs(row[2] - 0.5) < 1e-9


def test_record_suggestion_ignores_blank(tmp_path):
    h = _history(tmp_path)
    h.record_vocab_suggestion("", 0.1)
    n = h.conn.execute("SELECT COUNT(*) FROM vocab_suggestions").fetchone()[0]
    assert n == 0
