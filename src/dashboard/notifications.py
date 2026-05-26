"""Phase 9 — Notifications inbox: persistent log of every notify() call.

The toast pipeline writes here via a sink registered at daemon startup. The
dashboard reads here to render the inbox + bell badge unread count.
"""
from __future__ import annotations

import time


def insert(conn, level: str, title: str, body: str) -> int:
    level = (level or "info").strip().lower()
    if level not in ("info", "warning", "error"):
        level = "info"
    cur = conn.execute(
        "INSERT INTO notifications(ts, level, title, body) VALUES (?, ?, ?, ?)",
        (time.time(), level, title or "", body or ""),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


def list_recent(conn, limit: int = 200) -> list[dict]:
    rows = conn.execute(
        "SELECT id, ts, level, title, body, read_at "
        "FROM notifications ORDER BY id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [
        {"id": r[0], "ts": r[1], "level": r[2], "title": r[3],
         "body": r[4], "read_at": r[5]}
        for r in rows
    ]


def unread_count(conn) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE read_at IS NULL"
    ).fetchone()
    return int(row[0]) if row else 0


def mark_read(conn, notif_id: int) -> bool:
    if notif_id <= 0:
        return False
    with conn:
        cur = conn.execute(
            "UPDATE notifications SET read_at = ? WHERE id = ? AND read_at IS NULL",
            (time.time(), notif_id),
        )
    return cur.rowcount > 0


def mark_all_read(conn) -> int:
    with conn:
        cur = conn.execute(
            "UPDATE notifications SET read_at = ? WHERE read_at IS NULL",
            (time.time(),),
        )
    return int(cur.rowcount)
