"""PR-B — additive migrations: user_rating, latency_ms, command_log."""
from __future__ import annotations

import sqlite3
import pytest

from src.history import History


def _h(tmp_path):
    return History(str(tmp_path / "h.db"))


# --- Idempotent column / table presence ------------------------------------

def test_user_rating_column_present(tmp_path):
    h = _h(tmp_path)
    cols = [r[1] for r in h.conn.execute("PRAGMA table_info(dictations)").fetchall()]
    assert "user_rating" in cols


def test_latency_ms_column_present(tmp_path):
    h = _h(tmp_path)
    cols = [r[1] for r in h.conn.execute("PRAGMA table_info(dictations)").fetchall()]
    assert "latency_ms" in cols


def test_command_log_table_present(tmp_path):
    h = _h(tmp_path)
    tables = [r[0] for r in h.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "command_log" in tables


def test_migrations_are_idempotent(tmp_path):
    """Re-opening the DB twice shouldn't error or duplicate columns."""
    db = str(tmp_path / "h.db")
    History(db)  # first open
    History(db)  # second open — must not raise
    h = History(db)
    cols = [r[1] for r in h.conn.execute("PRAGMA table_info(dictations)").fetchall()]
    # Each column appears exactly once.
    assert cols.count("user_rating") == 1
    assert cols.count("latency_ms") == 1


def test_migrates_old_db_without_new_columns(tmp_path):
    """Simulate an install upgraded from a pre-PR-B version: open a DB whose
    dictations table predates user_rating/latency_ms, confirm migration adds
    them without data loss."""
    db = tmp_path / "h.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE dictations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            window_title TEXT,
            style TEXT,
            language TEXT,
            duration_ms INTEGER,
            raw_text TEXT,
            cleaned_text TEXT
        );
        INSERT INTO dictations(ts, raw_text, cleaned_text) VALUES (1.0, 'r', 'c');
    """)
    conn.commit(); conn.close()
    h = History(str(db))
    cols = [r[1] for r in h.conn.execute("PRAGMA table_info(dictations)").fetchall()]
    assert "user_rating" in cols and "latency_ms" in cols
    # Existing row survives migration.
    row = h.conn.execute("SELECT raw_text, cleaned_text FROM dictations").fetchone()
    assert row == ("r", "c")


# --- History.log(latency_ms=...) ------------------------------------------

def test_log_persists_latency_ms(tmp_path):
    h = _h(tmp_path)
    did = h.log(window_title="t", style="default", language="en", duration_ms=10,
                raw_text="r", cleaned_text="c", latency_ms=420)
    row = h.conn.execute("SELECT latency_ms FROM dictations WHERE id = ?", (did,)).fetchone()
    assert row[0] == 420


def test_log_latency_defaults_to_null(tmp_path):
    h = _h(tmp_path)
    did = h.log(window_title="t", style="default", language="en", duration_ms=10,
                raw_text="r", cleaned_text="c")
    row = h.conn.execute("SELECT latency_ms FROM dictations WHERE id = ?", (did,)).fetchone()
    assert row[0] is None


# --- rate_dictation() ------------------------------------------------------

def test_rate_dictation_writes_approval(tmp_path):
    h = _h(tmp_path)
    did = h.log(window_title="t", style="default", language="en", duration_ms=10,
                raw_text="r", cleaned_text="c")
    assert h.rate_dictation(did, 1) is True
    row = h.conn.execute("SELECT user_rating FROM dictations WHERE id = ?", (did,)).fetchone()
    assert row[0] == 1


def test_rate_dictation_writes_bad(tmp_path):
    h = _h(tmp_path)
    did = h.log(window_title="t", style="default", language="en", duration_ms=10,
                raw_text="r", cleaned_text="c")
    h.rate_dictation(did, -1)
    row = h.conn.execute("SELECT user_rating FROM dictations WHERE id = ?", (did,)).fetchone()
    assert row[0] == -1


def test_rate_dictation_clears(tmp_path):
    h = _h(tmp_path)
    did = h.log(window_title="t", style="default", language="en", duration_ms=10,
                raw_text="r", cleaned_text="c")
    h.rate_dictation(did, 1)
    h.rate_dictation(did, None)
    row = h.conn.execute("SELECT user_rating FROM dictations WHERE id = ?", (did,)).fetchone()
    assert row[0] is None


def test_rate_dictation_rejects_garbage(tmp_path):
    h = _h(tmp_path)
    did = h.log(window_title="t", style="default", language="en", duration_ms=10,
                raw_text="r", cleaned_text="c")
    with pytest.raises(ValueError):
        h.rate_dictation(did, 7)


def test_rate_dictation_missing_id(tmp_path):
    h = _h(tmp_path)
    assert h.rate_dictation(9999, 1) is False


# --- log_command + recent_commands ----------------------------------------

def test_log_command_round_trip(tmp_path):
    h = _h(tmp_path)
    a = h.log_command(body="select all", action_type="hotkey", action_value="ctrl+a",
                      label="select all", ok=True)
    b = h.log_command(body="weird", action_type="unknown", action_value="",
                      label=None, ok=False)
    items = h.recent_commands()
    assert [i["id"] for i in items] == [b, a]
    assert items[0]["ok"] is False
    assert items[1]["action_value"] == "ctrl+a"
    assert items[1]["label"] == "select all"


def test_recent_commands_limit(tmp_path):
    h = _h(tmp_path)
    for i in range(5):
        h.log_command(body=f"cmd{i}", action_type="key", action_value="enter")
    items = h.recent_commands(limit=3)
    assert len(items) == 3
