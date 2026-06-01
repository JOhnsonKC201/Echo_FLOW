"""Dictionary / custom vocabulary CRUD.

Stores user-curated terms in the `custom_vocabulary` SQLite table. These
terms are merged into the Whisper decoder bias (initial_prompt) alongside
the patterns mined automatically by PatternMiner. Empty table = no
explicit terms (mined vocab still applies).

Computer-first design: this is a desktop-user surface. Mobile cannot
write to this table — no bridge route exists. The dashboard is the only
producer.
"""
from __future__ import annotations

import sqlite3
import time


def list_terms(conn: sqlite3.Connection) -> list[dict]:
    """Return [{"id", "term", "added_at"}, ...] sorted alphabetically."""
    cur = conn.execute(
        "SELECT id, term, added_at FROM custom_vocabulary "
        "ORDER BY term COLLATE NOCASE ASC"
    )
    return [{"id": r[0], "term": r[1], "added_at": r[2]} for r in cur]


def add_term(conn: sqlite3.Connection, term: str) -> int:
    """Insert a term. Idempotent — duplicates are no-ops returning the existing id."""
    term = (term or "").strip()
    if not term:
        raise ValueError("term cannot be empty")
    if len(term) > 80:
        raise ValueError("term too long (max 80 chars)")
    try:
        cur = conn.execute(
            "INSERT INTO custom_vocabulary(term, added_at) VALUES (?, ?)",
            (term, time.time()),
        )
        conn.commit()
        return cur.lastrowid or 0
    except sqlite3.IntegrityError:
        row = conn.execute(
            "SELECT id FROM custom_vocabulary WHERE term = ?", (term,)
        ).fetchone()
        return row[0] if row else 0


def delete_term(conn: sqlite3.Connection, term_id: int) -> bool:
    """Delete by id. Returns True if a row was removed."""
    cur = conn.execute("DELETE FROM custom_vocabulary WHERE id = ?", (term_id,))
    conn.commit()
    return cur.rowcount > 0


def bulk_import(conn: sqlite3.Connection, raw: str) -> dict:
    """Import newline- or comma-separated terms. Returns counts dict."""
    # Tolerant parser: split on commas and newlines; strip; dedupe case-insensitively.
    candidates: list[str] = []
    for chunk in (raw or "").replace(",", "\n").splitlines():
        c = chunk.strip()
        if c:
            candidates.append(c)
    seen_lower: set[str] = set()
    added = 0
    skipped_dup = 0
    skipped_invalid = 0
    for term in candidates:
        low = term.lower()
        if low in seen_lower:
            skipped_dup += 1
            continue
        seen_lower.add(low)
        try:
            # add_term is idempotent: for a term already in the DB it returns
            # the EXISTING id (> 0), so new_id > 0 cannot distinguish "inserted"
            # from "already present". Check existence first so pre-existing
            # terms count as duplicates, not additions.
            exists = conn.execute(
                "SELECT 1 FROM custom_vocabulary WHERE term = ? LIMIT 1", (term,)
            ).fetchone()
            if exists:
                skipped_dup += 1
                continue
            add_term(conn, term)
            added += 1
        except ValueError:
            skipped_invalid += 1
    return {"added": added, "duplicates": skipped_dup, "invalid": skipped_invalid,
            "total_seen": len(candidates)}


def all_terms(conn: sqlite3.Connection) -> list[str]:
    """Plain list of just the term strings — what main.py consumes for biasing."""
    return [r[1] for r in conn.execute("SELECT id, term FROM custom_vocabulary")]
