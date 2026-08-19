"""Tests for `Retriever._backfill` — the startup pass that embeds old rows.

The batched commit in that loop is not a performance nicety. Its own comment
records why it exists: sqlite3 opens an implicit write transaction on the first
UPDATE and holds it until commit, so embedding a few thousand rows in one
transaction pins an exclusive write lock for about a minute, and every dictation
logged in that window used to die with "database is locked" and be lost.

That makes "does the commit actually fire" a correctness question rather than a
tuning one, which is what these tests pin down.

`retrieval.embed` is monkeypatched everywhere here: the real call loads a
sentence-transformers model, and tests must stay offline.
"""
from __future__ import annotations

import sqlite3

import numpy as np

from src import retrieval
from src.history import History


# --- helpers -------------------------------------------------------------------

class _CountingConn:
    """Proxies a real connection and counts commits.

    sqlite3.Connection is a C type and rejects attribute patching, so the only
    way to observe commit cadence is to wrap it.
    """

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self.commits = 0

    def execute(self, *args, **kwargs):
        return self._real.execute(*args, **kwargs)

    def commit(self) -> None:
        self.commits += 1
        self._real.commit()

    def close(self) -> None:
        self._real.close()


class _CountingRetriever(retrieval.Retriever):
    """A Retriever whose connections record how often they were committed."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.conns: list[_CountingConn] = []

    def _conn(self) -> _CountingConn:  # type: ignore[override]
        conn = _CountingConn(sqlite3.connect(self.db_path, timeout=30.0))
        self.conns.append(conn)
        return conn

    @property
    def commits(self) -> int:
        return sum(c.commits for c in self.conns)


def _seed_unembedded(conn: sqlite3.Connection, count: int) -> None:
    """Insert `count` rows with text but no embedding, which is what backfill looks for."""
    for n in range(count):
        conn.execute(
            "INSERT INTO dictations(ts, window_title, style, raw_text, cleaned_text, "
            "embedding, source) VALUES (?,?,?,?,?,?,?)",
            (1000.0 + n, "Notepad", "default", f"row {n}", f"Row {n}.", None, "desktop"),
        )
    conn.commit()


def _vec() -> np.ndarray:
    v = np.asarray([1, 0, 0, 0], dtype=np.float32)
    return v / np.linalg.norm(v)


def _build(tmp_path, monkeypatch, *, rows: int, fail_on: set[int]):
    """A retriever over `rows` unembedded rows, where 1-based positions in
    `fail_on` raise from embed(). Returns (retriever, db_path)."""
    db_path = tmp_path / "h.db"
    hist = History(str(db_path))
    _seed_unembedded(hist.conn, rows)
    hist.conn.close()

    seen = {"n": 0}

    def fake_embed(_text: str) -> np.ndarray:
        seen["n"] += 1
        if seen["n"] in fail_on:
            raise RuntimeError("model choked on this row")
        return _vec()

    monkeypatch.setattr(retrieval, "embed", fake_embed)

    cfg = retrieval.RetrievalConfig(enabled=True, backfill_on_startup=True)
    return _CountingRetriever(str(db_path), cfg), str(db_path)


def _embedded_count(db_path: str) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM dictations WHERE embedding IS NOT NULL"
        ).fetchone()[0]


# --- the batched commit ---------------------------------------------------------

def test_backfill_commits_on_batch_boundaries(tmp_path, monkeypatch):
    """The happy path: 120 rows commit at 50, at 100, and once at the end."""
    r, _ = _build(tmp_path, monkeypatch, rows=120, fail_on=set())
    r._backfill()
    assert r.commits == 3


def test_a_failing_row_does_not_swallow_its_batch_commit(tmp_path, monkeypatch):
    """A row that fails to embed must not cancel the commit for the batch it
    lands on.

    Rows 50 and 100 are exactly the batch boundaries. Skipping straight to the
    next row on failure also skips the boundary check, so both commits are lost
    and every write collapses back into the single end-of-loop commit. That is
    the long exclusive write lock the batching exists to prevent, so the failure
    mode is silent: the backfill still reports success while dictations logged
    during it are rejected as "database is locked".
    """
    r, _ = _build(tmp_path, monkeypatch, rows=120, fail_on={50, 100})
    r._backfill()
    assert r.commits == 3


def test_backfill_still_embeds_every_row_that_works(tmp_path, monkeypatch):
    """The two failures are skipped; the other 118 rows are written and durable."""
    r, db_path = _build(tmp_path, monkeypatch, rows=120, fail_on={50, 100})
    r._backfill()
    assert _embedded_count(db_path) == 118


def test_backfill_is_a_noop_when_every_row_already_has_an_embedding(tmp_path, monkeypatch):
    """Nothing to do means no transaction is opened at all."""
    r, _ = _build(tmp_path, monkeypatch, rows=10, fail_on=set())
    r._backfill()
    before = r.commits
    r2 = _CountingRetriever(r.db_path, r.cfg)
    r2._backfill()
    assert before > 0
    assert r2.commits == 0
