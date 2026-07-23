"""Dictionary-suggestion review: read / dismiss / promote.

The daemon records low-confidence content words into `vocab_suggestions`
(see `History.record_vocab_suggestion`). This module is the dashboard side:
list the pending ones, dismiss the noise, or promote a real term into
`custom_vocabulary` (which feeds the Whisper decoder bias on the next config
reload). Mirrors the shape of `vocabulary.py`.
"""
from __future__ import annotations

import sqlite3

from . import vocabulary


def list_suggestions(conn: sqlite3.Connection, limit: int = 25,
                     include_dismissed: bool = False) -> list[dict]:
    """Pending suggestions, most-fumbled first (count desc, then lowest prob)."""
    where = "" if include_dismissed else "WHERE dismissed = 0"
    rows = conn.execute(
        f"SELECT term_lc, term, count, avg_prob, dismissed FROM vocab_suggestions "
        f"{where} ORDER BY count DESC, avg_prob ASC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [{"term_lc": r[0], "term": r[1], "count": r[2],
             "avg_prob": r[3], "dismissed": bool(r[4])} for r in rows]


def count_pending(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM vocab_suggestions WHERE dismissed = 0"
    ).fetchone()
    return int(row[0]) if row else 0


def dismiss(conn: sqlite3.Connection, term_lc: str) -> bool:
    """Hide a suggestion the user doesn't want pinned. Returns True if updated."""
    term_lc = (term_lc or "").strip().lower()
    if not term_lc:
        return False
    cur = conn.execute(
        "UPDATE vocab_suggestions SET dismissed = 1 WHERE term_lc = ?", (term_lc,)
    )
    conn.commit()
    return cur.rowcount > 0


def promote(conn: sqlite3.Connection, term_lc: str) -> str:
    """Add the suggested term to the dictionary and drop it from suggestions.

    Returns the promoted term string (as originally cased), or "" if the
    suggestion was not found. Raises ValueError from `vocabulary.add_term` on an
    invalid term (too long) — the caller surfaces it as a flash message.
    """
    term_lc = (term_lc or "").strip().lower()
    if not term_lc:
        return ""
    row = conn.execute(
        "SELECT term FROM vocab_suggestions WHERE term_lc = ?", (term_lc,)
    ).fetchone()
    if not row:
        return ""
    term = row[0]
    vocabulary.add_term(conn, term)                 # idempotent
    conn.execute("DELETE FROM vocab_suggestions WHERE term_lc = ?", (term_lc,))
    conn.commit()
    return term
