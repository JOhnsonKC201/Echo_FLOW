"""Tests for the data layer behind the Tk review-queue dialog (src/editor.py).

The dialog itself is Tkinter and not unit-tested (no display in CI), but the
query that feeds it — `review_queue` — and the list-label helper `_review_snippet`
are pure and were extracted to module scope precisely so they can be tested
headless. `review_queue` is the contract: lowest-quality, *un-edited* dictations,
worst first, bounded, and graceful on a bad DB.
"""
from __future__ import annotations

from src import editor
from src.history import History


def _seed(h, *, cleaned, original, quality, raw="raw text", ts=1000.0):
    cur = h.conn.execute(
        "INSERT INTO dictations(ts, raw_text, cleaned_text, original_cleaned, quality_score) "
        "VALUES (?,?,?,?,?)",
        (ts, raw, cleaned, original, quality),
    )
    h.conn.commit()
    return cur.lastrowid


# --- review_queue -------------------------------------------------------------

def test_review_queue_worst_first_only_unedited(tmp_path):
    """Worst-quality first; edited rows (cleaned != original) and rows with no
    quality score are excluded even when they'd otherwise sort to the top."""
    db = str(tmp_path / "h.db")
    h = History(db)
    low = _seed(h, cleaned="low", original="low", quality=10.0)
    mid = _seed(h, cleaned="mid", original="mid", quality=55.0)
    _seed(h, cleaned="fixed", original="orig", quality=5.0)   # edited → excluded
    _seed(h, cleaned="nq", original="nq", quality=None)        # no score → excluded
    h.conn.close()

    rows = editor.review_queue(db)

    assert [r[0] for r in rows] == [low, mid]


def test_review_queue_respects_limit(tmp_path):
    db = str(tmp_path / "h.db")
    h = History(db)
    for i in range(5):
        _seed(h, cleaned=f"c{i}", original=f"c{i}", quality=float(i))
    h.conn.close()

    assert len(editor.review_queue(db, n=3)) == 3


def test_review_queue_empty_db_returns_empty(tmp_path):
    """A freshly seeded but empty history has nothing to review."""
    db = str(tmp_path / "h.db")
    History(db).conn.close()
    assert editor.review_queue(db) == []


def test_review_queue_bad_path_degrades_to_empty(tmp_path):
    """A path with no dictations table must yield [] rather than raising — the
    dialog shows 'nothing to review' instead of crashing the daemon thread."""
    assert editor.review_queue(str(tmp_path / "not_a_real.db")) == []


# --- _review_snippet ----------------------------------------------------------

def test_review_snippet_flattens_newlines_and_strips():
    assert editor._review_snippet("  hello\nworld  ") == "hello world"


def test_review_snippet_ellipsizes_past_width():
    out = editor._review_snippet("x" * 100, width=70)
    assert out.endswith("…")
    assert len(out) == 71  # 70 chars + the ellipsis


def test_review_snippet_handles_none():
    assert editor._review_snippet(None) == ""
