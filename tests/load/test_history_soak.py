"""history.db under a write storm: the daemon's shared connection, the
learning miner's own connections, and readers on separate connections, all at
once. sqlite allows one writer at a time; what must hold is that nobody gives
up with "database is locked" and no write is lost."""
from __future__ import annotations

import threading

import pytest

from src import graph
from src.history import History
from src.learn import Learner, LearningConfig, PatternMiner
from tests.load.conftest import hammer, seed_history

pytestmark = pytest.mark.load

WRITES_EACH = 20


def test_mixed_writers_and_readers_never_hit_database_is_locked(tmp_path):
    db = str(tmp_path / "history.db")
    h = History(db)
    seed_history(h, 500)
    miner = PatternMiner(db)
    learner = Learner(db, LearningConfig(enabled=True))
    logged = []
    log_lock = threading.Lock()

    def work(i):
        role = i % 4
        if role == 0:        # the dictation hot path, shared connection
            for n in range(WRITES_EACH):
                rid = h.log(window_title="w", style="medium", language="en",
                            duration_ms=900, raw_text=f"soak {i} {n}",
                            cleaned_text=f"Soak {i} {n}.", source="desktop")
                with log_lock:
                    logged.append(rid)
        elif role == 1:      # learning miner, its own connection per call
            for n in range(WRITES_EACH):
                miner.record(f"teh quick {i} brwn fox {n}", f"the quick {i} brown fox {n}")
        elif role == 2:      # graph page, reads by path
            for _ in range(3):
                graph._load_rows(db)
        else:                # cleanup prompt builder, reads by path
            for _ in range(WRITES_EACH):
                learner.recent_examples("medium", 5)
        return 200

    report = hammer(work)
    print(report.summary())

    assert report.errors == [], report.errors[:3]
    assert len(logged) == len(set(logged)) == 25 * WRITES_EACH
    count = h.conn.execute(
        "SELECT COUNT(*) FROM dictations WHERE raw_text LIKE 'soak %'").fetchone()[0]
    assert count == 25 * WRITES_EACH
