"""Two connections upgrading learned_patterns at once. Each reads the column
list, sees user_count / teacher_count missing, and adds them; the slower one
used to die with "duplicate column name" and that dictation's learning was
dropped. Found by tests/load/test_history_soak.py."""
from __future__ import annotations

import sqlite3

import pytest

from src import learn

pytestmark = pytest.mark.unit

OLD_SCHEMA = """
CREATE TABLE learned_patterns (
    trigger TEXT NOT NULL, replacement TEXT NOT NULL,
    success INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0, PRIMARY KEY (trigger, replacement))
"""


class _StaleView:
    """A connection that still sees the pre-upgrade column list, as the loser
    of the race does: it read PRAGMA table_info before the winner's ALTER."""

    def __init__(self, conn, stale_cols):
        self._conn = conn
        self._stale = stale_cols

    def execute(self, sql, *args):
        if sql.startswith("PRAGMA table_info(learned_patterns)"):
            return _Rows(self._stale)
        return self._conn.execute(sql, *args)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


def _old_db(tmp_path):
    db = tmp_path / "h.db"
    conn = sqlite3.connect(db)
    conn.execute(OLD_SCHEMA)
    conn.commit()
    return db, conn


def test_losing_the_upgrade_race_is_harmless(tmp_path):
    db, winner = _old_db(tmp_path)
    stale = winner.execute("PRAGMA table_info(learned_patterns)").fetchall()
    learn._ensure_patterns_table(winner)        # adds both columns
    winner.commit()

    loser = sqlite3.connect(db)
    learn._ensure_patterns_table(_StaleView(loser, stale))   # must not raise

    cols = {r[1] for r in loser.execute("PRAGMA table_info(learned_patterns)")}
    assert {"user_count", "teacher_count"} <= cols


def test_record_still_lands_when_it_loses_the_race(tmp_path, monkeypatch):
    db, winner = _old_db(tmp_path)
    stale = winner.execute("PRAGMA table_info(learned_patterns)").fetchall()
    learn._ensure_patterns_table(winner)
    winner.commit()
    winner.close()

    real_connect = sqlite3.connect
    monkeypatch.setattr(learn.PatternMiner, "_conn",
                        lambda self: _ClosableStale(real_connect(self.db_path), stale))
    added = learn.PatternMiner(str(db)).record("teh fox", "the fox")
    assert added >= 1
    check = real_connect(db)
    assert check.execute(
        "SELECT replacement FROM learned_patterns WHERE trigger = 'teh'").fetchone() == ("the",)


class _ClosableStale(_StaleView):
    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def test_only_the_duplicate_column_error_is_tolerated(tmp_path):
    db, conn = _old_db(tmp_path)

    class _LockedOnAlter(_StaleView):
        def execute(self, sql, *args):
            if sql.startswith("ALTER TABLE"):
                raise sqlite3.OperationalError("database is locked")
            return super().execute(sql, *args)

    stale = conn.execute("PRAGMA table_info(learned_patterns)").fetchall()
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        learn._ensure_patterns_table(_LockedOnAlter(conn, stale))
