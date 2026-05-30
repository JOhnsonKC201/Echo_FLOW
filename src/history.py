"""SQLite log of every dictation: raw + cleaned + context."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS dictations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    window_title TEXT,
    style TEXT,
    language TEXT,
    duration_ms INTEGER,
    raw_text TEXT,
    cleaned_text TEXT
);
"""

POST_MIGRATION_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_dictations_ts ON dictations(ts)",
    "CREATE INDEX IF NOT EXISTS idx_dictations_style ON dictations(style)",
    "CREATE INDEX IF NOT EXISTS idx_dictations_emb_model ON dictations(embedding_model)",
)


class History:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        # WAL mode: readers (dashboard) never block writers (dictation daemon)
        # and vice versa. Important now that the dashboard reads dictations
        # live while the daemon is logging new rows.
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        # Step 1: ensure base table exists (without new columns)
        self.conn.executescript(BASE_SCHEMA)
        # Step 2: idempotent column migrations
        try:
            cols = [r[1] for r in self.conn.execute("PRAGMA table_info(dictations)").fetchall()]
            if "embedding" not in cols:
                self.conn.execute("ALTER TABLE dictations ADD COLUMN embedding BLOB")
            if "embedding_model" not in cols:
                self.conn.execute("ALTER TABLE dictations ADD COLUMN embedding_model TEXT")
            # Self-grading columns (added 2026-05-20)
            if "quality_score" not in cols:
                self.conn.execute("ALTER TABLE dictations ADD COLUMN quality_score REAL")
            if "quality_breakdown" not in cols:
                self.conn.execute("ALTER TABLE dictations ADD COLUMN quality_breakdown TEXT")
            # original_cleaned preserves the model's output even after editor.py overwrites
            # cleaned_text with a user correction — needed for calibration.
            if "original_cleaned" not in cols:
                self.conn.execute("ALTER TABLE dictations ADD COLUMN original_cleaned TEXT")
            # Source provenance — distinguishes trusted desktop dictations from
            # mobile-bridge submissions (which must not poison RAG by default).
            if "source" not in cols:
                self.conn.execute("ALTER TABLE dictations ADD COLUMN source TEXT NOT NULL DEFAULT 'desktop'")
            # Senior-rewrite additive columns (2026-05-26):
            # user_rating — NULL untouched, 1 approved, -1 marked bad (Inbox).
            # latency_ms — end-to-end release→paste, populated by main._do_dictation.
            if "user_rating" not in cols:
                self.conn.execute("ALTER TABLE dictations ADD COLUMN user_rating INTEGER")
            if "latency_ms" not in cols:
                self.conn.execute("ALTER TABLE dictations ADD COLUMN latency_ms INTEGER")
            # Dashboard-managed collections (added 2026-05-25). These shadow
            # the older config.yaml-based snippets so the UI is the source of
            # truth. Empty tables = fall back to config defaults.
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS custom_vocabulary (
                    id INTEGER PRIMARY KEY,
                    term TEXT NOT NULL UNIQUE,
                    added_at REAL NOT NULL
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS user_snippets (
                    id INTEGER PRIMARY KEY,
                    code TEXT NOT NULL UNIQUE,
                    expansion TEXT NOT NULL,
                    added_at REAL NOT NULL
                )
            """)
            # Notifications inbox (Phase 9). Every notify.notify() call is
            # appended here so the dashboard's bell badge has a persistent log.
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    level TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    read_at REAL
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_notifications_ts ON notifications(ts)"
            )
            # Command Mode log (Phase 13's dispatch site appends here).
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS command_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    body TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    action_value TEXT NOT NULL,
                    label TEXT,
                    ok INTEGER NOT NULL DEFAULT 1
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cmdlog_ts ON command_log(ts)"
            )
            # Action Mode log (Phase 14 — voice_actions dispatch site appends here).
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS voice_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    body TEXT NOT NULL,
                    handler TEXT NOT NULL,
                    args TEXT,
                    label TEXT,
                    ok INTEGER NOT NULL DEFAULT 1,
                    error TEXT
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_vactions_ts ON voice_actions(ts)"
            )
            # Action Mode user targets (Phase 14+ — dashboard-managed app/folder
            # shortcuts). `kind` is 'app' or 'folder'; (kind, name) is unique so
            # saving an existing name updates it. Read-through at dispatch time
            # so edits in the dashboard take effect without a daemon restart.
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS action_targets (
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    target TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (kind, name)
                )
            """)
        except Exception as e:
            # Column migrations should never fail in practice (idempotent via PRAGMA check).
            # If they do, log it loudly so we don't end up with a half-migrated schema.
            import logging
            logging.getLogger("wispr.history").error("schema migration failed: %s", e)
        # Step 3: indexes (now that all columns exist)
        for stmt in POST_MIGRATION_INDEXES:
            try:
                self.conn.execute(stmt)
            except Exception:
                pass
        # Step 4: A/B provider comparison log (added 2026-05-20)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS provider_ab_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                raw_text TEXT,
                primary_provider TEXT NOT NULL,
                primary_text TEXT,
                primary_quality REAL,
                alt_provider TEXT NOT NULL,
                alt_text TEXT,
                alt_quality REAL,
                winner TEXT
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ab_ts ON provider_ab_log(ts)"
        )
        # Step 5: Knowledge graph paradigm shift — notes, tags, action items (2026-05-20)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dictation_id INTEGER,
                title TEXT NOT NULL,
                description TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                color TEXT,
                created_at REAL NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS dictation_tags (
                dictation_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                confidence REAL NOT NULL,
                confirmed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (dictation_id, tag_id)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS action_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dictation_id INTEGER,
                text TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                completed_at REAL
            )
        """)
        for stmt in (
            "CREATE INDEX IF NOT EXISTS idx_notes_updated ON notes(updated_at)",
            "CREATE INDEX IF NOT EXISTS idx_dtags_dict ON dictation_tags(dictation_id)",
            "CREATE INDEX IF NOT EXISTS idx_dtags_tag ON dictation_tags(tag_id)",
            "CREATE INDEX IF NOT EXISTS idx_actions_open ON action_items(completed, created_at)",
            # Indexes for source-aware queries — source filtering in the
            # learner (recent_examples / personal_vocabulary) and the
            # teacher-compare view (which joins on raw_text) both scan
            # without these. Negligible on small DBs; required on large.
            "CREATE INDEX IF NOT EXISTS idx_dictations_source ON dictations(source)",
            "CREATE INDEX IF NOT EXISTS idx_dictations_raw ON dictations(raw_text)",
            "CREATE INDEX IF NOT EXISTS idx_dictations_style_ts ON dictations(style, ts DESC)",
        ):
            try:
                self.conn.execute(stmt)
            except Exception:
                pass
        self.conn.commit()

    def log(self, *, window_title: str, style: str, language: str,
            duration_ms: int, raw_text: str, cleaned_text: str,
            embedding: bytes | None = None,
            embedding_model: str | None = None,
            quality_score: float | None = None,
            quality_breakdown: str | None = None,
            source: str = "desktop",
            latency_ms: int | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO dictations(ts, window_title, style, language, duration_ms, "
            "raw_text, cleaned_text, embedding, embedding_model, "
            "quality_score, quality_breakdown, original_cleaned, source, latency_ms) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), window_title, style, language, duration_ms,
             raw_text, cleaned_text, embedding, embedding_model,
             quality_score, quality_breakdown, cleaned_text, source, latency_ms),
        )
        self.conn.commit()
        return cur.lastrowid or 0

    def rate_dictation(self, dictation_id: int, rating: int | None) -> bool:
        """Set user_rating to 1 (approved), -1 (bad), or NULL (cleared).
        Returns True if a row was updated."""
        if rating is not None and rating not in (1, -1):
            raise ValueError("rating must be 1, -1, or None")
        with self.conn:
            cur = self.conn.execute(
                "UPDATE dictations SET user_rating = ? WHERE id = ?",
                (rating, int(dictation_id)),
            )
        return cur.rowcount > 0

    def log_command(self, *, body: str, action_type: str, action_value: str,
                    label: str | None = None, ok: bool = True) -> int:
        """Append a row to command_log. Best-effort — caller swallows errors."""
        cur = self.conn.execute(
            "INSERT INTO command_log(ts, body, action_type, action_value, label, ok) "
            "VALUES (?,?,?,?,?,?)",
            (time.time(), body or "", action_type, action_value, label, 1 if ok else 0),
        )
        self.conn.commit()
        return cur.lastrowid or 0

    def log_action(self, *, body: str, handler: str, args_json: str | None = None,
                   label: str | None = None, ok: bool = True,
                   error: str | None = None) -> int:
        """Append a row to voice_actions (Phase 14). Best-effort — caller
        swallows errors, mirroring log_command."""
        cur = self.conn.execute(
            "INSERT INTO voice_actions(ts, body, handler, args, label, ok, error) "
            "VALUES (?,?,?,?,?,?,?)",
            (time.time(), body or "", handler, args_json, label,
             1 if ok else 0, error),
        )
        self.conn.commit()
        return cur.lastrowid or 0

    def recent_actions(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, ts, body, handler, args, label, ok, error "
            "FROM voice_actions ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [
            {"id": r[0], "ts": r[1], "body": r[2], "handler": r[3],
             "args": r[4], "label": r[5], "ok": bool(r[6]), "error": r[7]}
            for r in rows
        ]

    # --- Action Mode user targets (dashboard-managed app/folder shortcuts) ----

    def set_action_target(self, kind: str, name: str, target: str) -> None:
        """Insert or update an app/folder shortcut. `name` is the spoken key
        (stored lower-cased); `kind` is 'app' or 'folder'. Raises ValueError on
        bad input so the caller can surface a clean message."""
        kind = (kind or "").strip().lower()
        if kind not in ("app", "folder"):
            raise ValueError("kind must be 'app' or 'folder'")
        name = (name or "").strip().lower()
        target = (target or "").strip()
        if not name or not target:
            raise ValueError("name and target are required")
        with self.conn:
            self.conn.execute(
                "INSERT INTO action_targets(kind, name, target, updated_at) "
                "VALUES (?,?,?,?) "
                "ON CONFLICT(kind, name) DO UPDATE SET "
                "target=excluded.target, updated_at=excluded.updated_at",
                (kind, name, target, time.time()),
            )

    def delete_action_target(self, kind: str, name: str) -> bool:
        """Remove a shortcut. Returns True if a row was deleted."""
        kind = (kind or "").strip().lower()
        name = (name or "").strip().lower()
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM action_targets WHERE kind=? AND name=?",
                (kind, name),
            )
        return cur.rowcount > 0

    def list_action_targets(self, kind: str | None = None) -> list[dict]:
        """List shortcuts, optionally filtered by kind. Newest first."""
        if kind:
            rows = self.conn.execute(
                "SELECT kind, name, target, updated_at FROM action_targets "
                "WHERE kind=? ORDER BY name",
                ((kind or "").strip().lower(),),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT kind, name, target, updated_at FROM action_targets "
                "ORDER BY kind, name"
            ).fetchall()
        return [{"kind": r[0], "name": r[1], "target": r[2], "updated_at": r[3]}
                for r in rows]

    def recent_commands(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, ts, body, action_type, action_value, label, ok "
            "FROM command_log ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [
            {"id": r[0], "ts": r[1], "body": r[2], "action_type": r[3],
             "action_value": r[4], "label": r[5], "ok": bool(r[6])}
            for r in rows
        ]

    # --- Notes ---

    def add_note(self, *, dictation_id: int | None, title: str,
                 description: str | None = None) -> int:
        now = time.time()
        cur = self.conn.execute(
            "INSERT INTO notes(dictation_id, title, description, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (dictation_id, title, description, now, now),
        )
        self.conn.commit()
        return cur.lastrowid or 0

    def update_note(self, note_id: int, *, title: str | None = None,
                    description: str | None = None) -> None:
        fields = []
        values: list = []
        if title is not None:
            fields.append("title = ?")
            values.append(title)
        if description is not None:
            fields.append("description = ?")
            values.append(description)
        if not fields:
            return
        fields.append("updated_at = ?")
        values.append(time.time())
        values.append(note_id)
        self.conn.execute(
            f"UPDATE notes SET {', '.join(fields)} WHERE id = ?", values
        )
        self.conn.commit()

    def list_notes(self, limit: int = 200) -> list[tuple]:
        return self.conn.execute(
            "SELECT id, dictation_id, title, description, created_at, updated_at "
            "FROM notes ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def get_note(self, note_id: int) -> tuple | None:
        return self.conn.execute(
            "SELECT id, dictation_id, title, description, created_at, updated_at "
            "FROM notes WHERE id = ?",
            (note_id,),
        ).fetchone()

    # --- Tags ---

    def _ensure_tag(self, name: str) -> int:
        """Get-or-create the tag and return its id."""
        name = name.strip().lower()
        if not name:
            raise ValueError("tag name cannot be empty")
        row = self.conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        if row:
            return int(row[0])
        cur = self.conn.execute(
            "INSERT INTO tags(name, color, created_at) VALUES (?, ?, ?)",
            (name, None, time.time()),
        )
        self.conn.commit()
        return cur.lastrowid or 0

    def set_tag(self, dictation_id: int, name: str, *, source: str = "manual",
                confidence: float = 1.0, confirmed: bool = True) -> None:
        tag_id = self._ensure_tag(name)
        self.conn.execute(
            "INSERT INTO dictation_tags(dictation_id, tag_id, source, confidence, confirmed) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(dictation_id, tag_id) DO UPDATE SET "
            "  confidence = MAX(confidence, excluded.confidence),"
            "  confirmed = CASE WHEN excluded.confirmed = 1 THEN 1 ELSE confirmed END,"
            "  source = CASE WHEN excluded.confirmed = 1 THEN 'manual' ELSE source END",
            (dictation_id, tag_id, source, float(confidence), 1 if confirmed else 0),
        )
        self.conn.commit()

    def remove_tag(self, dictation_id: int, name: str) -> None:
        name = name.strip().lower()
        self.conn.execute(
            "DELETE FROM dictation_tags WHERE dictation_id = ? AND tag_id = "
            "(SELECT id FROM tags WHERE name = ?)",
            (dictation_id, name),
        )
        self.conn.commit()

    def get_tags_for_dictation(self, dictation_id: int,
                                confirmed_only: bool = False) -> list[tuple]:
        """Returns [(tag_name, source, confidence, confirmed), ...]."""
        sql = (
            "SELECT t.name, dt.source, dt.confidence, dt.confirmed "
            "FROM dictation_tags dt JOIN tags t ON t.id = dt.tag_id "
            "WHERE dt.dictation_id = ?"
        )
        if confirmed_only:
            sql += " AND dt.confirmed = 1"
        sql += " ORDER BY dt.confirmed DESC, dt.confidence DESC"
        return self.conn.execute(sql, (dictation_id,)).fetchall()

    def known_tag_names(self, confirmed_only: bool = True) -> set[str]:
        """All tag names that appear at least once (optionally only confirmed)."""
        if confirmed_only:
            rows = self.conn.execute(
                "SELECT DISTINCT t.name FROM tags t "
                "JOIN dictation_tags dt ON dt.tag_id = t.id "
                "WHERE dt.confirmed = 1"
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT name FROM tags").fetchall()
        return {r[0] for r in rows}

    # --- Action items ---

    def add_action_item(self, dictation_id: int | None, text: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO action_items(dictation_id, text, completed, created_at) "
            "VALUES (?, ?, 0, ?)",
            (dictation_id, text, time.time()),
        )
        self.conn.commit()
        return cur.lastrowid or 0

    def mark_action_complete(self, action_id: int, completed: bool = True) -> None:
        self.conn.execute(
            "UPDATE action_items SET completed = ?, completed_at = ? WHERE id = ?",
            (1 if completed else 0, time.time() if completed else None, action_id),
        )
        self.conn.commit()

    def action_items_for_dictation(self, dictation_id: int) -> list[tuple]:
        return self.conn.execute(
            "SELECT id, text, completed, created_at, completed_at "
            "FROM action_items WHERE dictation_id = ? ORDER BY id",
            (dictation_id,),
        ).fetchall()

    def open_action_items(self, limit: int = 50) -> list[tuple]:
        return self.conn.execute(
            "SELECT id, dictation_id, text, created_at "
            "FROM action_items WHERE completed = 0 ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def log_ab(self, *, raw_text: str, primary_provider: str, primary_text: str,
               primary_quality: float | None, alt_provider: str, alt_text: str,
               alt_quality: float | None, winner: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO provider_ab_log(ts, raw_text, primary_provider, primary_text, "
            "primary_quality, alt_provider, alt_text, alt_quality, winner) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (time.time(), raw_text, primary_provider, primary_text, primary_quality,
             alt_provider, alt_text, alt_quality, winner),
        )
        self.conn.commit()
        return cur.lastrowid or 0

    def ab_tally(self, since_seconds: int = 7 * 86400) -> dict:
        """Summarize provider_ab_log over the last N seconds."""
        try:
            cur = self.conn.execute(
                "SELECT winner, COUNT(*) FROM provider_ab_log "
                "WHERE ts >= ? GROUP BY winner",
                (time.time() - since_seconds,),
            )
            rows = cur.fetchall()
        except Exception:
            return {}
        return {w or "tie": n for w, n in rows}

    def recent(self, n: int = 20):
        cur = self.conn.execute(
            "SELECT ts, window_title, style, cleaned_text FROM dictations ORDER BY ts DESC LIMIT ?",
            (n,),
        )
        return cur.fetchall()
