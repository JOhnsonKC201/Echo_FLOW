"""Scratchpad — long-form named documents you can dictate into and edit later.

Each scratchpad has a title (auto-generated from first sentence if blank)
and a body. Optional dictate-into-scratchpad mode: when a scratchpad is
"targeted", the next dictation appends to its body instead of pasting
into the focused window.

Schema: scratchpads (id, title, body, created_at, updated_at,
source_dictation_id NULL — link the first dictation that created it).
"""
from __future__ import annotations

import sqlite3
import time


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scratchpads (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            source_dictation_id INTEGER NULL
        )
    """)
    conn.commit()


def _auto_title(body: str, *, fallback: str = "Untitled") -> str:
    """Use first sentence (max ~60 chars). Wispr-style."""
    text = (body or "").strip()
    if not text:
        return fallback
    # Cut at first end-of-sentence punctuation.
    for end in (". ", "! ", "? ", "\n"):
        idx = text.find(end)
        if 0 < idx < 120:
            text = text[:idx]
            break
    text = text.strip()
    if len(text) > 60:
        text = text[:57].rstrip() + "…"
    return text or fallback


def list_scratchpads(conn: sqlite3.Connection) -> list[dict]:
    ensure_table(conn)
    cur = conn.execute(
        "SELECT id, title, body, created_at, updated_at "
        "FROM scratchpads ORDER BY updated_at DESC"
    )
    return [
        {"id": r[0], "title": r[1], "body": r[2],
         "created_at": r[3], "updated_at": r[4]}
        for r in cur
    ]


def get_scratchpad(conn: sqlite3.Connection, pad_id: int) -> dict | None:
    ensure_table(conn)
    r = conn.execute(
        "SELECT id, title, body, created_at, updated_at, source_dictation_id "
        "FROM scratchpads WHERE id = ?", (pad_id,)
    ).fetchone()
    if not r:
        return None
    return {"id": r[0], "title": r[1], "body": r[2],
            "created_at": r[3], "updated_at": r[4],
            "source_dictation_id": r[5]}


def create_scratchpad(
    conn: sqlite3.Connection,
    *,
    title: str = "",
    body: str = "",
    source_dictation_id: int | None = None,
) -> int:
    ensure_table(conn)
    now = time.time()
    title = (title or "").strip() or _auto_title(body)
    cur = conn.execute(
        "INSERT INTO scratchpads(title, body, created_at, updated_at, source_dictation_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (title, body or "", now, now, source_dictation_id),
    )
    conn.commit()
    return cur.lastrowid or 0


def save_scratchpad(
    conn: sqlite3.Connection,
    pad_id: int,
    *,
    title: str | None = None,
    body: str | None = None,
) -> bool:
    ensure_table(conn)
    existing = get_scratchpad(conn, pad_id)
    if not existing:
        return False
    new_title = existing["title"] if title is None else (title.strip() or _auto_title(body or existing["body"]))
    new_body = existing["body"] if body is None else body
    conn.execute(
        "UPDATE scratchpads SET title = ?, body = ?, updated_at = ? WHERE id = ?",
        (new_title, new_body, time.time(), pad_id),
    )
    conn.commit()
    return True


def append_to_scratchpad(
    conn: sqlite3.Connection,
    pad_id: int,
    text: str,
    *,
    separator: str = "\n",
) -> bool:
    """Append text (with separator) and bump updated_at."""
    existing = get_scratchpad(conn, pad_id)
    if not existing:
        return False
    new_body = existing["body"]
    if new_body and text:
        new_body = new_body.rstrip() + separator + text
    else:
        new_body = (new_body or "") + (text or "")
    conn.execute(
        "UPDATE scratchpads SET body = ?, updated_at = ? WHERE id = ?",
        (new_body, time.time(), pad_id),
    )
    conn.commit()
    return True


def delete_scratchpad(conn: sqlite3.Connection, pad_id: int) -> bool:
    ensure_table(conn)
    cur = conn.execute("DELETE FROM scratchpads WHERE id = ?", (pad_id,))
    conn.commit()
    return cur.rowcount > 0
