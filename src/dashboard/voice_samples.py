"""Voice samples CRUD — the user's own writing, used to seed the "My Voice"
humanize pass.

Stored in the `voice_samples` SQLite table (created in history.py's migration).
Each row is a free block of text the user actually wrote; `src/voice_profile.py`
assembles the enabled rows (plus mined dictation exemplars) into the voice
profile that the humanize pass conditions on.

Computer-first: desktop-only producer, mirroring snippets.py. This is personal
user text, so it lives in the local DB — never config.yaml, never a plaintext
file.
"""
from __future__ import annotations

import sqlite3
import time

# A single sample is a paragraph or a few — cap it so a stray paste can't bloat
# the prompt. The profile builder additionally budgets the TOTAL across samples.
MAX_SAMPLE_CHARS = 8000


def _clean(content: str) -> str:
    content = (content or "").strip()
    if not content:
        raise ValueError("sample cannot be empty")
    if len(content) > MAX_SAMPLE_CHARS:
        raise ValueError(f"sample too long (max {MAX_SAMPLE_CHARS} chars)")
    return content


def list_samples(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        "SELECT id, content, source, enabled, char_len, added_at "
        "FROM voice_samples ORDER BY added_at DESC, id DESC"
    )
    return [
        {"id": r[0], "content": r[1], "source": r[2],
         "enabled": bool(r[3]), "char_len": r[4], "added_at": r[5]}
        for r in cur
    ]


def enabled_texts(conn: sqlite3.Connection) -> list[str]:
    """The content of enabled samples, newest first — the profile builder's
    Section A source. Kept separate from list_samples so the builder never
    depends on the dashboard's richer row shape."""
    cur = conn.execute(
        "SELECT content FROM voice_samples WHERE enabled = 1 "
        "ORDER BY added_at DESC, id DESC"
    )
    return [r[0] for r in cur]


def add_sample(conn: sqlite3.Connection, content: str,
               source: str = "pasted") -> int:
    content = _clean(content)
    cur = conn.execute(
        "INSERT INTO voice_samples(content, source, enabled, char_len, added_at) "
        "VALUES (?, ?, 1, ?, ?)",
        (content, source or "pasted", len(content), time.time()),
    )
    conn.commit()
    return cur.lastrowid or 0


def update_sample(conn: sqlite3.Connection, sample_id: int, content: str) -> bool:
    content = _clean(content)
    cur = conn.execute(
        "UPDATE voice_samples SET content = ?, char_len = ?, added_at = ? "
        "WHERE id = ?",
        (content, len(content), time.time(), sample_id),
    )
    conn.commit()
    return cur.rowcount > 0


def set_enabled(conn: sqlite3.Connection, sample_id: int, enabled: bool) -> bool:
    cur = conn.execute(
        "UPDATE voice_samples SET enabled = ? WHERE id = ?",
        (1 if enabled else 0, sample_id),
    )
    conn.commit()
    return cur.rowcount > 0


def delete_sample(conn: sqlite3.Connection, sample_id: int) -> bool:
    cur = conn.execute("DELETE FROM voice_samples WHERE id = ?", (sample_id,))
    conn.commit()
    return cur.rowcount > 0


def bulk_import(conn: sqlite3.Connection, raw: str) -> dict:
    """Split a paste into samples on blank lines — one sample per paragraph
    block. Blocks over the length cap are skipped (counted invalid), never
    truncated silently.
    """
    text = (raw or "").replace("\r\n", "\n")
    blocks = [b.strip() for b in text.split("\n\n")]
    added = 0
    invalid = 0
    seen = 0
    for block in blocks:
        if not block:
            continue
        seen += 1
        try:
            add_sample(conn, block)
            added += 1
        except ValueError:
            invalid += 1
    return {"added": added, "invalid": invalid, "total_seen": seen}
