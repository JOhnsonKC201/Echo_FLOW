"""history.db under a write storm: the daemon's shared connection, the
learning miner's own connections, and readers on separate connections, all at
once. sqlite allows one writer at a time; what must hold is that nobody gives
up with "database is locked" and no write is lost."""
from __future__ import annotations

import threading
import time

import pytest

from src import dbconn, graph
from src.history import History
from src.learn import Learner, LearningConfig, PatternMiner
from tests.load.conftest import hammer, seed_history

pytestmark = pytest.mark.load

WRITES_EACH = 20


def _storm(db: str) -> None:
    """25 dictation writers, 25 miner writers and 50 readers at once."""
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


def test_mixed_writers_and_readers_never_hit_database_is_locked(tmp_path):
    _storm(str(tmp_path / "history.db"))


def test_writers_take_turns_so_none_waits_out_its_timeout(tmp_path, monkeypatch):
    """sqlite's busy handler does not queue: a waiting writer sleeps for longer
    and longer stretches and retries, and whoever is awake when the lock frees
    takes it. In a burst one writer can lose that race until its timeout runs
    out, which is how CI saw "database is locked" despite a 30 s timeout.

    A 0.25 s timeout makes the starvation show on a fast machine too (about 60
    lost writes per run without the gate). Writers that queue on
    dbconn.write_gate() never wait inside sqlite at all, so the timeout no
    longer matters."""
    monkeypatch.setattr(dbconn, "DB_TIMEOUT_S", 0.25)
    _storm(str(tmp_path / "history.db"))


def test_a_busy_dictation_log_does_not_starve_the_miner(tmp_path, monkeypatch):
    """The shared connection is one sqlite writer however many threads use
    it, and the next thread is already awake when a commit lands, so a miner
    polling from its own connection keeps arriving after the lock is taken
    again. History's writes queue on the same gate as the miner's."""
    monkeypatch.setattr(dbconn, "DB_TIMEOUT_S", 0.1)
    db = str(tmp_path / "history.db")
    h = History(db)
    miner = PatternMiner(db)
    miner.record("teh warmup", "the warmup")       # tables exist before the race
    done = threading.Event()
    deadline = time.monotonic() + 60               # a starved miner fails, not hangs

    def work(i):
        if i == 0:
            try:
                for n in range(WRITES_EACH):
                    miner.record(f"teh quick {n} brwn fox", f"the quick {n} brown fox")
            finally:
                done.set()
            return 200 if time.monotonic() < deadline else 504
        n = 0
        while not done.is_set() and time.monotonic() < deadline:
            h.log(window_title="w", style="medium", language="en", duration_ms=900,
                  raw_text=f"busy {i} {n}", cleaned_text=f"Busy {i} {n}.")
            n += 1
        return 200

    report = hammer(work, n=40)
    print(report.summary())

    assert report.errors == [], report.errors[:3]
    assert report.outcomes[0].status == 200, "the miner never got its turn"
