"""One way to open history.db, and one queue for writing to it.

sqlite allows one writer at a time and, by default, a waiting connection gives
up after 5 s with "database is locked". The daemon's shared connection, the
learning miner, grading, the graph and the editor all write or read the same
file, and on a slow disk a queue of writers easily passes 5 s. Waiting longer
costs nothing when the lock is free and saves the write when it is not.

A longer timeout is not enough on its own, because sqlite's busy handler does
not queue its waiters (see write_gate), so the daemon's own writers also take
turns on write_gate().
"""
from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager

# Matches the retrieval backfill, which already used 30 s for this reason.
DB_TIMEOUT_S = 30.0

# Reentrant so a gated writer can call another gated writer on its own thread.
_WRITE_GATE = threading.RLock()


def connect(db_path, **kwargs) -> sqlite3.Connection:
    kwargs.setdefault("timeout", DB_TIMEOUT_S)
    return sqlite3.connect(db_path, **kwargs)


@contextmanager
def write_gate() -> Iterator[None]:
    """Hold this around a write transaction on history.db from the daemon.

    A connection waiting for sqlite's write lock sleeps 1, 2, 5 ... up to 100 ms
    between tries, and the lock goes to whoever happens to try right after it
    frees. Nobody keeps a place in line, so in a burst one writer can lose the
    race over and over until its timeout is spent, however long the timeout
    is: a 100-writer soak lost writes after 30 s on CI and after 0.25 s here.
    A threading lock wakes the next waiter as soon as it is released, so the
    daemon's writers take turns and none of them waits inside sqlite.

    Readers never need it, since WAL lets them read while someone writes. A
    writer that skips it (or another process) still gets DB_TIMEOUT_S.
    Do not call out to slow work (models, network) while holding it.
    """
    with _WRITE_GATE:
        yield
