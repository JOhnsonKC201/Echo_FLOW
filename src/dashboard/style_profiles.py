"""Style profiles section — read/edit per-app-context cleanup style.

Style profiles live in a new SQLite table `style_profiles` (analogous to
user_snippets — UI is authoritative). The daemon reads via a provider
injected into Cleaner.pick_style. Empty table = fall back to config
cleanup.profiles defaults.

A profile maps a list of window-title matchers to a style name (one of
SYSTEM_PROMPTS keys: default, code, casual, email, prompt). First match
wins; mirrors the existing Cleaner.pick_style semantics.
"""
from __future__ import annotations

import json
import sqlite3
import time


_VALID_STYLES = ("polished", "default", "code", "casual", "email", "prompt")


def ensure_table(conn: sqlite3.Connection) -> None:
    """Idempotent migration so old DBs upgrade on first dashboard hit."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS style_profiles (
            id INTEGER PRIMARY KEY,
            position INTEGER NOT NULL,
            style TEXT NOT NULL,
            matchers TEXT NOT NULL,         -- JSON array of substring matchers
            updated_at REAL NOT NULL
        )
    """)
    conn.commit()


def list_profiles(conn: sqlite3.Connection) -> list[dict]:
    ensure_table(conn)
    cur = conn.execute(
        "SELECT id, position, style, matchers FROM style_profiles "
        "ORDER BY position ASC, id ASC"
    )
    out = []
    for rid, pos, style, matchers_json in cur:
        try:
            matchers = json.loads(matchers_json or "[]")
        except Exception:
            matchers = []
        out.append({"id": rid, "position": pos, "style": style, "matchers": matchers})
    return out


def replace_all(conn: sqlite3.Connection, profiles: list[dict]) -> None:
    """Replace the entire profile list atomically.

    Each profile is {"style": str, "matchers": list[str]}. position is
    assigned by list order (first listed = first checked, mirroring
    Cleaner.pick_style's "first hit wins").
    """
    ensure_table(conn)
    for p in profiles:
        style = p.get("style", "default")
        if style not in _VALID_STYLES:
            raise ValueError(f"invalid style {style!r}; allowed: {_VALID_STYLES}")
    now = time.time()
    with conn:
        conn.execute("DELETE FROM style_profiles")
        for pos, p in enumerate(profiles):
            conn.execute(
                "INSERT INTO style_profiles(position, style, matchers, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (pos, p["style"], json.dumps(p.get("matchers", [])), now),
            )


def pick_style(
    conn: sqlite3.Connection,
    window_title: str,
    *,
    config_default: str = "default",
) -> str:
    """Return the style that matches a window title, or the fallback.

    Used by the daemon via a provider hook. Empty profile table -> returns
    config_default (the daemon then falls back to its config-driven logic).
    """
    title = (window_title or "").lower()
    profiles = list_profiles(conn)
    fallback: str | None = None
    for p in profiles:
        if not p["matchers"]:
            # Empty matchers -> catch-all (mirrors config.yaml convention).
            fallback = p["style"]
            continue
        if any(m.lower() in title for m in p["matchers"]):
            return p["style"]
    return fallback if fallback is not None else config_default


def migrate_profiles_to_polished(conn: sqlite3.Connection) -> bool:
    """One-shot: upgrade every existing profile row to the 'polished' style.

    The table was seeded from an old config.yaml whose catch-all was the
    minimal-touch 'default' (never fixes grammar) — and seed_from_config never
    re-syncs, so the user's later config change to 'polished' silently never
    took effect. The 'prompt' style is left alone (PE mode is a different
    feature, not a polish level).

    Guarded by an app_meta flag so it runs exactly once: a user who later
    deliberately picks 'default'/'code' in the dashboard is never overridden
    again. Returns True when rows were migrated.
    """
    flag = "style_all_polished_v1"
    ensure_table(conn)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT)"
    )
    if conn.execute("SELECT 1 FROM app_meta WHERE key = ?", (flag,)).fetchone():
        return False
    with conn:
        cur = conn.execute(
            "UPDATE style_profiles SET style = 'polished', updated_at = ? "
            "WHERE style != 'prompt'",
            (time.time(),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_meta(key, value) VALUES(?, ?)", (flag, "1")
        )
    return cur.rowcount > 0


def seed_from_config(conn: sqlite3.Connection, config_profiles: list) -> int:
    """One-shot: seed empty table from config.yaml cleanup.profiles."""
    ensure_table(conn)
    existing = conn.execute("SELECT 1 FROM style_profiles LIMIT 1").fetchone()
    if existing:
        return 0
    seeded = 0
    now = time.time()
    for pos, p in enumerate(config_profiles or []):
        style = (p or {}).get("style", "default")
        matchers = (p or {}).get("match", [])
        if not isinstance(matchers, list):
            continue
        if style not in _VALID_STYLES:
            continue
        conn.execute(
            "INSERT INTO style_profiles(position, style, matchers, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (pos, style, json.dumps(matchers), now),
        )
        seeded += 1
    conn.commit()
    return seeded
