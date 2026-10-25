"""Every connection to history.db waits for the write lock instead of failing.

sqlite3's default busy timeout is 5 s. Locally the history soak test never
reached it, but on CI's slower runners writers queued past 5 s and died with
"database is locked", which drops a learned correction or a grade."""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from src import dbconn

SRC = Path(__file__).resolve().parent.parent / "src"


@pytest.mark.unit
def test_no_module_opens_history_db_with_the_default_timeout():
    bare = []
    for path in SRC.rglob("*.py"):
        if path.name == "dbconn.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"sqlite3\.connect\(", line) and "timeout" not in line:
                bare.append(f"{path.relative_to(SRC)}:{n}")
    assert bare == [], "use dbconn.connect(): " + ", ".join(bare)


@pytest.mark.unit
def test_dbconn_waits_well_past_the_sqlite_default():
    assert dbconn.DB_TIMEOUT_S >= 30


@pytest.mark.load
def test_a_learned_pattern_write_waits_out_a_long_writer(tmp_path):
    from src.history import History
    from src.learn import PatternMiner
    db = str(tmp_path / "history.db")
    History(db).conn.close()
    miner = PatternMiner(db)
    miner.record("teh fox", "the fox")          # table exists before the hold

    holder = sqlite3.connect(db, timeout=1, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")           # take the write lock
    released = threading.Event()

    def release_later():
        time.sleep(6)                           # longer than sqlite's 5 s default
        holder.rollback()
        holder.close()
        released.set()

    threading.Thread(target=release_later, daemon=True).start()
    assert miner.record("teh cat", "the cat") >= 1
    assert released.is_set()
