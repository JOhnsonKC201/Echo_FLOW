"""Concurrency regression test for the shared History connection.

History opens its sqlite connection with check_same_thread=False and is written
from many daemon threads (per-dictation logging, post-processing, A/B shadow,
teacher distillation) while the dashboard reads/writes the same connection.
Python's sqlite3 only serializes individual C-API calls, not the connection's
implicit-transaction state machine, so before the _SafeConn lock wrapper a
hammer of concurrent execute+commit dropped rows and raised
"cannot start a transaction within a transaction" / API-misuse errors.
"""
from __future__ import annotations

import threading


def test_concurrent_writers_lose_no_rows(tmp_path):
    from src.history import History

    h = History(str(tmp_path / "hist.db"))

    n_threads = 12
    per_thread = 60
    errors: list[Exception] = []

    def worker(tid: int) -> None:
        try:
            for i in range(per_thread):
                h.log(
                    window_title=f"t{tid}",
                    style="default",
                    language="en",
                    duration_ms=10,
                    raw_text=f"raw {tid}-{i}",
                    cleaned_text=f"clean {tid}-{i}",
                )
        except Exception as e:  # pragma: no cover - only on regression
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent writes raised: {errors[:3]}"
    (count,) = h.conn.execute("SELECT COUNT(*) FROM dictations").fetchone()
    assert count == n_threads * per_thread
    h.conn.close()


def test_concurrent_reads_and_writes(tmp_path):
    """Dashboard-style reads racing the daemon's writes must not corrupt state."""
    from src.history import History

    h = History(str(tmp_path / "hist2.db"))
    stop = threading.Event()
    errors: list[Exception] = []

    def reader() -> None:
        try:
            while not stop.is_set():
                h.conn.execute("SELECT id, raw_text FROM dictations ORDER BY ts DESC LIMIT 20").fetchall()
        except Exception as e:  # pragma: no cover
            errors.append(e)

    def writer() -> None:
        try:
            for i in range(200):
                h.log(
                    window_title="w", style="default", language="en",
                    duration_ms=1, raw_text=f"r{i}", cleaned_text=f"c{i}",
                )
        except Exception as e:  # pragma: no cover
            errors.append(e)

    rt = [threading.Thread(target=reader) for _ in range(3)]
    wt = [threading.Thread(target=writer) for _ in range(3)]
    for t in rt + wt:
        t.start()
    for t in wt:
        t.join()
    stop.set()
    for t in rt:
        t.join()

    assert not errors, f"concurrent read/write raised: {errors[:3]}"
    (count,) = h.conn.execute("SELECT COUNT(*) FROM dictations").fetchone()
    assert count == 600
    h.conn.close()
