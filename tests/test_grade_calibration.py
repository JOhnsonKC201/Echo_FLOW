"""Tests for src/grade.py calibrate_from_edits — quality-score sanity check.

calibrate_from_edits computes Pearson r between the grader's quality_score and
the edit distance from the model's output (original_cleaned) to the user's
correction (cleaned_text). A healthy grader yields a *negative* r: rows it
scored high needed only small fixes. The function returns None whenever it
can't compute a meaningful r — missing columns, fewer than 5 edited rows, or
zero variance on either axis — so callers never act on a degenerate statistic.

Rows are seeded with synthetic edits whose size is controlled exactly: the
"correction" appends k filler words to the model output, so a longer append
means a strictly larger SequenceMatcher edit distance. Pairing larger k with
lower quality_score forces a deterministic negative correlation.
"""
from __future__ import annotations

import sqlite3

from src.grade import calibrate_from_edits
from src.history import History


def _seed_edit(conn, *, ts: float, quality: float, pad_words: int) -> None:
    """One dictation the user corrected: cleaned_text differs from
    original_cleaned by `pad_words` appended filler words."""
    orig = "alpha beta gamma delta epsilon zeta"
    corrected = orig + " pad" * pad_words
    conn.execute(
        "INSERT INTO dictations(ts, raw_text, cleaned_text, original_cleaned, "
        "quality_score) VALUES (?,?,?,?,?)",
        (ts, "raw", corrected, orig, quality),
    )


def _db(tmp_path) -> tuple[History, str]:
    path = str(tmp_path / "h.db")
    return History(path), path


def test_returns_negative_r_when_high_quality_means_small_edits(tmp_path):
    """Monotone relationship (higher score → smaller correction) must come out
    as r < 0 — that's the signal the dashboard reads as 'grader is working'."""
    h, path = _db(tmp_path)
    for ts, quality, pad in [
        (1.0, 95.0, 1), (2.0, 85.0, 2), (3.0, 70.0, 4),
        (4.0, 55.0, 7), (5.0, 40.0, 11),
    ]:
        _seed_edit(h.conn, ts=ts, quality=quality, pad_words=pad)
    h.conn.commit()

    r = calibrate_from_edits(path)

    assert r is not None
    assert -1.0 <= r <= 1.0
    assert r < 0  # quality and edit size move in opposite directions


def test_returns_positive_r_when_grader_is_backwards(tmp_path):
    """Sanity on the sign convention: if HIGH scores got the BIG edits, r must
    flip positive — calibration has to be able to expose a broken grader."""
    h, path = _db(tmp_path)
    for ts, quality, pad in [
        (1.0, 95.0, 11), (2.0, 85.0, 7), (3.0, 70.0, 4),
        (4.0, 55.0, 2), (5.0, 40.0, 1),
    ]:
        _seed_edit(h.conn, ts=ts, quality=quality, pad_words=pad)
    h.conn.commit()

    r = calibrate_from_edits(path)

    assert r is not None
    assert r > 0


def test_returns_none_with_fewer_than_five_samples(tmp_path):
    """Four edited rows is below the documented minimum — a Pearson r over a
    handful of points is noise, so the function refuses."""
    h, path = _db(tmp_path)
    for ts, quality, pad in [(1.0, 95.0, 1), (2.0, 80.0, 3),
                             (3.0, 60.0, 6), (4.0, 40.0, 9)]:
        _seed_edit(h.conn, ts=ts, quality=quality, pad_words=pad)
    h.conn.commit()

    assert calibrate_from_edits(path) is None


def test_unedited_rows_do_not_count_toward_the_minimum(tmp_path):
    """Only rows the user actually changed qualify (original_cleaned !=
    cleaned_text). Four real edits plus many untouched rows is still
    insufficient."""
    h, path = _db(tmp_path)
    for ts, quality, pad in [(1.0, 95.0, 1), (2.0, 80.0, 3),
                             (3.0, 60.0, 6), (4.0, 40.0, 9)]:
        _seed_edit(h.conn, ts=ts, quality=quality, pad_words=pad)
    for i in range(10):  # untouched: original_cleaned == cleaned_text
        h.conn.execute(
            "INSERT INTO dictations(ts, raw_text, cleaned_text, original_cleaned, "
            "quality_score) VALUES (?,?,?,?,?)",
            (10.0 + i, "raw", "same text", "same text", 75.0),
        )
    h.conn.commit()

    assert calibrate_from_edits(path) is None


def test_returns_none_when_quality_has_zero_variance(tmp_path):
    """Identical scores on every row make the denominator 0 — Pearson r is
    undefined, and the function must say None instead of dividing by zero."""
    h, path = _db(tmp_path)
    for ts, pad in [(1.0, 1), (2.0, 2), (3.0, 4), (4.0, 7), (5.0, 11)]:
        _seed_edit(h.conn, ts=ts, quality=70.0, pad_words=pad)
    h.conn.commit()

    assert calibrate_from_edits(path) is None


def test_returns_none_when_edit_distance_has_zero_variance(tmp_path):
    """Same degenerate case on the other axis: every correction identical in
    size → dy == 0 → None."""
    h, path = _db(tmp_path)
    for ts, quality in [(1.0, 95.0), (2.0, 85.0), (3.0, 70.0),
                        (4.0, 55.0), (5.0, 40.0)]:
        _seed_edit(h.conn, ts=ts, quality=quality, pad_words=3)
    h.conn.commit()

    assert calibrate_from_edits(path) is None


def test_returns_none_when_grading_columns_are_missing(tmp_path):
    """A pre-migration DB (no quality_score / original_cleaned columns) is a
    real state on old installs — calibration must bail, not raise."""
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE dictations (id INTEGER PRIMARY KEY, ts REAL, "
        "raw_text TEXT, cleaned_text TEXT)"
    )
    conn.commit()
    conn.close()

    assert calibrate_from_edits(path) is None


def test_returns_none_on_empty_database(tmp_path):
    """Brand-new install: schema exists but zero dictations."""
    _h, path = _db(tmp_path)

    assert calibrate_from_edits(path) is None
