"""Transforms — manageable cleanup rewrites with optional hotkey arming.

A transform is a named system prompt that overrides the default cleanup
for the next dictation. Built-ins (Polish, Prompt Engineer) seed on
first open; users can add custom transforms via the dashboard.

Schema: transforms table holds (id, name, system_prompt, hotkey, builtin, enabled).

Hotkey arming is wired through src/hotkey.py's dynamic registration —
when a user binds Win+Alt+1 to "Polish formal", pressing that combo
arms the transform for exactly the next dictation. See Phase 6 wiring
in src/main.py for the integration point.
"""
from __future__ import annotations

import sqlite3


# Built-in transforms seeded on first run. The system prompts intentionally
# match (or extend) cleanup.SYSTEM_PROMPTS so the UI is consistent.
BUILTINS = [
    {
        "name": "Polish",
        "system_prompt": (
            "Polish this dictation. Fix grammar and punctuation, remove "
            "filler words, but preserve the speaker's voice and meaning "
            "exactly. Output only the polished text."
        ),
        "hotkey": None,
    },
    {
        "name": "Prompt Engineer",
        "system_prompt": (
            "Rewrite this rough spoken request as a clear, single-paragraph "
            "instruction suitable to hand to an AI coding agent. Do NOT add "
            "requirements the user didn't state. Output only the polished "
            "request."
        ),
        "hotkey": None,
    },
    {
        "name": "Formal",
        "system_prompt": (
            "Rewrite this dictation in formal, professional prose. Expand "
            "contractions, remove slang, use complete sentences. Preserve "
            "the original meaning exactly. Output only the rewritten text."
        ),
        "hotkey": None,
    },
    # "My Voice" is SPECIAL: unlike the static transforms above, main.py
    # intercepts it by name and runs the live humanize pass (Cleaner.humanize)
    # over the CLEANED text using the user's voice profile — so it reflects
    # freshly-pasted samples and runs after cleanup, not as a raw override.
    # This prompt is documentation + a schema-valid non-empty fallback.
    {
        "name": "My Voice",
        "system_prompt": (
            "Rewrite this in my own writing voice, using my writing samples as "
            "the reference. Keep the meaning exactly the same. Output only the "
            "rewritten text."
        ),
        "hotkey": None,
    },
]


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transforms (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            system_prompt TEXT NOT NULL,
            hotkey TEXT,
            builtin INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.commit()


def list_transforms(conn: sqlite3.Connection) -> list[dict]:
    ensure_table(conn)
    cur = conn.execute(
        "SELECT id, name, system_prompt, hotkey, builtin, enabled "
        "FROM transforms ORDER BY builtin DESC, name COLLATE NOCASE ASC"
    )
    return [
        {"id": r[0], "name": r[1], "system_prompt": r[2], "hotkey": r[3],
         "builtin": bool(r[4]), "enabled": bool(r[5])}
        for r in cur
    ]


def get_transform(conn: sqlite3.Connection, transform_id: int) -> dict | None:
    ensure_table(conn)
    row = conn.execute(
        "SELECT id, name, system_prompt, hotkey, builtin, enabled "
        "FROM transforms WHERE id = ?", (transform_id,)
    ).fetchone()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "system_prompt": row[2],
            "hotkey": row[3], "builtin": bool(row[4]), "enabled": bool(row[5])}


def add_transform(
    conn: sqlite3.Connection,
    *,
    name: str,
    system_prompt: str,
    hotkey: str | None = None,
) -> int:
    ensure_table(conn)
    name = (name or "").strip()
    system_prompt = (system_prompt or "").strip()
    if not name:
        raise ValueError("name cannot be empty")
    if len(name) > 60:
        raise ValueError("name too long (max 60 chars)")
    if not system_prompt:
        raise ValueError("system_prompt cannot be empty")
    if len(system_prompt) > 8000:
        raise ValueError("system_prompt too long (max 8000 chars)")
    if hotkey is not None:
        hotkey = (hotkey or "").strip().lower() or None
        if hotkey:
            _validate_hotkey(hotkey)
            _check_hotkey_unique(conn, hotkey, exclude_id=None)
    try:
        cur = conn.execute(
            "INSERT INTO transforms(name, system_prompt, hotkey, builtin) "
            "VALUES (?, ?, ?, 0)",
            (name, system_prompt, hotkey),
        )
        conn.commit()
        return cur.lastrowid or 0
    except sqlite3.IntegrityError as e:
        raise ValueError(f"name {name!r} already exists") from e


def update_transform(
    conn: sqlite3.Connection,
    transform_id: int,
    *,
    name: str | None = None,
    system_prompt: str | None = None,
    hotkey: str | None = ...,
    enabled: bool | None = None,
) -> None:
    """Partial update. Pass hotkey=None to clear; pass hotkey=... (default) to leave unchanged."""
    ensure_table(conn)
    existing = get_transform(conn, transform_id)
    if not existing:
        raise ValueError(f"transform {transform_id} not found")
    fields = []
    params: list = []
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("name cannot be empty")
        if existing["builtin"] and name != existing["name"]:
            raise ValueError("cannot rename built-in transform")
        fields.append("name = ?")
        params.append(name)
    if system_prompt is not None:
        system_prompt = system_prompt.strip()
        if not system_prompt:
            raise ValueError("system_prompt cannot be empty")
        fields.append("system_prompt = ?")
        params.append(system_prompt)
    if hotkey is not ...:
        if hotkey:
            hotkey = hotkey.strip().lower()
            _validate_hotkey(hotkey)
            _check_hotkey_unique(conn, hotkey, exclude_id=transform_id)
            fields.append("hotkey = ?")
            params.append(hotkey)
        else:
            fields.append("hotkey = NULL")
    if enabled is not None:
        fields.append("enabled = ?")
        params.append(1 if enabled else 0)
    if not fields:
        return
    params.append(transform_id)
    conn.execute(f"UPDATE transforms SET {', '.join(fields)} WHERE id = ?", tuple(params))
    conn.commit()


def delete_transform(conn: sqlite3.Connection, transform_id: int) -> bool:
    ensure_table(conn)
    row = conn.execute(
        "SELECT builtin FROM transforms WHERE id = ?", (transform_id,)
    ).fetchone()
    if not row:
        return False
    if row[0]:
        raise ValueError("cannot delete built-in transform; disable it instead")
    conn.execute("DELETE FROM transforms WHERE id = ?", (transform_id,))
    conn.commit()
    return True


def seed_builtins(conn: sqlite3.Connection) -> int:
    """Idempotent: insert built-ins that don't exist by name."""
    ensure_table(conn)
    inserted = 0
    for b in BUILTINS:
        existing = conn.execute(
            "SELECT 1 FROM transforms WHERE name = ?", (b["name"],)
        ).fetchone()
        if existing:
            continue
        conn.execute(
            "INSERT INTO transforms(name, system_prompt, hotkey, builtin) "
            "VALUES (?, ?, ?, 1)",
            (b["name"], b["system_prompt"], b["hotkey"]),
        )
        inserted += 1
    conn.commit()
    return inserted


def find_by_hotkey(conn: sqlite3.Connection, combo: str) -> dict | None:
    """Look up the transform bound to a given hotkey combo (lowercased)."""
    ensure_table(conn)
    combo = (combo or "").strip().lower()
    if not combo:
        return None
    row = conn.execute(
        "SELECT id, name, system_prompt, hotkey, builtin, enabled "
        "FROM transforms WHERE hotkey = ? AND enabled = 1",
        (combo,),
    ).fetchone()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "system_prompt": row[2],
            "hotkey": row[3], "builtin": bool(row[4]), "enabled": bool(row[5])}


# --- Hotkey validation -------------------------------------------------------

# Conservative grammar: 1+ modifiers + 1 key, separated by '+'.
# Modifiers: ctrl alt shift win cmd. Keys: a-z, 0-9, f1-f24.
_ALLOWED_MODS = {"ctrl", "alt", "shift", "win", "cmd"}
_ALLOWED_KEY_PATTERN = None  # lazy compile


def _validate_hotkey(combo: str) -> None:
    import re as _re
    global _ALLOWED_KEY_PATTERN
    if _ALLOWED_KEY_PATTERN is None:
        _ALLOWED_KEY_PATTERN = _re.compile(r"^(?:[a-z0-9]|f[1-9]|f1[0-9]|f2[0-4])$")
    parts = combo.split("+")
    if len(parts) < 2:
        raise ValueError(
            f"hotkey {combo!r} must include at least one modifier (e.g. 'ctrl+alt+p')"
        )
    *mods, key = parts
    for m in mods:
        if m not in _ALLOWED_MODS:
            raise ValueError(f"unknown modifier {m!r} in hotkey {combo!r}")
    if len(set(mods)) != len(mods):
        raise ValueError(f"duplicate modifier in hotkey {combo!r}")
    if not _ALLOWED_KEY_PATTERN.match(key):
        raise ValueError(f"unsupported key {key!r} in hotkey {combo!r}")


def _check_hotkey_unique(
    conn: sqlite3.Connection,
    hotkey: str,
    *,
    exclude_id: int | None,
) -> None:
    sql = "SELECT id, name FROM transforms WHERE hotkey = ?"
    params: tuple = (hotkey,)
    if exclude_id is not None:
        sql += " AND id != ?"
        params = (hotkey, exclude_id)
    row = conn.execute(sql, params).fetchone()
    if row:
        raise ValueError(
            f"hotkey {hotkey!r} already bound to transform {row[1]!r}"
        )
