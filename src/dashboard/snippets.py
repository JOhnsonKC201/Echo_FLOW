"""User snippets CRUD — shortcode → expansion.

Stored in the `user_snippets` SQLite table (added in Phase 3's migration).
Shadows the config.yaml cleanup.snippets defaults: when the table is
non-empty, it's the source of truth; otherwise the config defaults apply.

Computer-first: desktop-only producer. Mobile cannot edit snippets.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Iterable


def list_snippets(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        "SELECT id, code, expansion, added_at FROM user_snippets "
        "ORDER BY code COLLATE NOCASE ASC"
    )
    return [{"id": r[0], "code": r[1], "expansion": r[2], "added_at": r[3]} for r in cur]


def add_snippet(conn: sqlite3.Connection, code: str, expansion: str) -> int:
    code = (code or "").strip()
    expansion = (expansion or "").strip()
    if not code:
        raise ValueError("code cannot be empty")
    if not expansion:
        raise ValueError("expansion cannot be empty")
    if len(code) > 40:
        raise ValueError("code too long (max 40 chars)")
    if len(expansion) > 500:
        raise ValueError("expansion too long (max 500 chars)")
    # Upsert semantics: editing the same code replaces the expansion.
    existing = conn.execute(
        "SELECT id FROM user_snippets WHERE code = ?", (code,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE user_snippets SET expansion = ?, added_at = ? WHERE id = ?",
            (expansion, time.time(), existing[0]),
        )
        conn.commit()
        return int(existing[0])
    cur = conn.execute(
        "INSERT INTO user_snippets(code, expansion, added_at) VALUES (?, ?, ?)",
        (code, expansion, time.time()),
    )
    conn.commit()
    return cur.lastrowid or 0


def delete_snippet(conn: sqlite3.Connection, snippet_id: int) -> bool:
    cur = conn.execute("DELETE FROM user_snippets WHERE id = ?", (snippet_id,))
    conn.commit()
    return cur.rowcount > 0


def bulk_import(conn: sqlite3.Connection, raw: str) -> dict:
    """Each non-blank line is `code = expansion` or `code -> expansion`.

    Tolerant: leading/trailing whitespace stripped, blank lines skipped.
    """
    lines = (raw or "").splitlines()
    added = 0
    updated = 0
    invalid = 0
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        sep = "->" if "->" in s else "=" if "=" in s else None
        if not sep:
            invalid += 1
            continue
        code, expansion = s.split(sep, 1)
        try:
            existing = conn.execute(
                "SELECT id FROM user_snippets WHERE code = ?", (code.strip(),)
            ).fetchone()
            add_snippet(conn, code, expansion)
            if existing:
                updated += 1
            else:
                added += 1
        except ValueError:
            invalid += 1
    return {"added": added, "updated": updated, "invalid": invalid,
            "total_seen": len(lines)}


def merged_snippet_map(
    conn: sqlite3.Connection,
    config_defaults: dict,
) -> dict:
    """Return the snippet mapping the daemon should actually use.

    If user_snippets is non-empty: that's the source of truth. The config
    defaults are ignored, on the principle that the UI is authoritative
    once the user opens it. (If they truly want the defaults back, they
    can re-import via the bulk-import UI.)

    If user_snippets is empty: return config_defaults verbatim so existing
    installs aren't broken by Phase 4's introduction of the table.
    """
    rows = list_snippets(conn)
    if not rows:
        return dict(config_defaults or {})
    return {r["code"]: r["expansion"] for r in rows}


def seed_from_config(conn: sqlite3.Connection, config_defaults: dict) -> int:
    """One-shot helper: populate empty table from config defaults.

    Idempotent — does nothing if any user snippets already exist.
    """
    if not config_defaults:
        return 0
    existing = conn.execute("SELECT 1 FROM user_snippets LIMIT 1").fetchone()
    if existing:
        return 0
    added = 0
    for code, expansion in config_defaults.items():
        try:
            add_snippet(conn, str(code), str(expansion))
            added += 1
        except ValueError:
            continue
    return added
