"""One way to open history.db from outside History's shared connection.

sqlite allows one writer at a time and, by default, a waiting connection gives
up after 5 s with "database is locked". The daemon's shared connection, the
learning miner, grading, the graph and the editor all write or read the same
file, and on a slow disk a queue of writers easily passes 5 s. Waiting longer
costs nothing when the lock is free and saves the write when it is not.
"""
from __future__ import annotations

import sqlite3

# Matches the retrieval backfill, which already used 30 s for this reason.
DB_TIMEOUT_S = 30.0


def connect(db_path, **kwargs) -> sqlite3.Connection:
    kwargs.setdefault("timeout", DB_TIMEOUT_S)
    return sqlite3.connect(db_path, **kwargs)
