"""SQLite log of every dictation: raw + cleaned + context."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


class _SafeCursor:
    """Wraps a sqlite3.Cursor so its fetch/iter operations also serialize on the
    connection's lock — the fetch happens after ``execute`` returns, so without
    this a concurrent write on another thread could interleave with a read."""

    __slots__ = ("_cur", "_lock")

    def __init__(self, cur, lock):
        object.__setattr__(self, "_cur", cur)
        object.__setattr__(self, "_lock", lock)

    def __getattr__(self, name):
        attr = getattr(self._cur, name)
        if callable(attr):
            def _locked(*a, **k):
                with self._lock:
                    return attr(*a, **k)
            return _locked
        return attr

    def __iter__(self):
        with self._lock:
            return iter(self._cur.fetchall())


class _SafeConn:
    """Serializes every operation on a shared ``check_same_thread=False``
    connection behind a single re-entrant lock.

    The dictation daemon writes from several background threads while the Flask
    dashboard reads/writes the same connection. Python's sqlite3 only serializes
    individual C-API calls, not the connection's implicit-transaction state
    machine, so concurrent ``execute``+``commit`` could corrupt that state
    ("cannot start a transaction within a transaction") and drop rows. Routing
    all access through one lock makes the shared connection thread-safe with no
    changes to callers (including ``with conn:`` transaction blocks)."""

    def __init__(self, conn):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_lock", threading.RLock())

    def execute(self, *a, **k):
        with self._lock:
            return _SafeCursor(self._conn.execute(*a, **k), self._lock)

    def executemany(self, *a, **k):
        with self._lock:
            return _SafeCursor(self._conn.executemany(*a, **k), self._lock)

    def executescript(self, *a, **k):
        with self._lock:
            return _SafeCursor(self._conn.executescript(*a, **k), self._lock)

    def cursor(self, *a, **k):
        with self._lock:
            return _SafeCursor(self._conn.cursor(*a, **k), self._lock)

    def commit(self):
        with self._lock:
            return self._conn.commit()

    def rollback(self):
        with self._lock:
            return self._conn.rollback()

    def close(self):
        with self._lock:
            return self._conn.close()

    def __enter__(self):
        # `with conn:` opens an implicit transaction and commits/rolls back on
        # exit. Hold the lock for the whole block so the transaction is atomic
        # relative to other threads. RLock => nested execute() calls re-enter.
        self._lock.acquire()
        try:
            self._conn.__enter__()
        except BaseException:
            self._lock.release()
            raise
        return self

    def __exit__(self, *exc):
        try:
            return self._conn.__exit__(*exc)
        finally:
            self._lock.release()

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        setattr(self._conn, name, value)


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
            # model_pred (MODEL-SHADOW) is the intent model's JSON record for the
            # utterance — agreement with the regex on hits, the would-have-fired
            # guess on misses (handler = 'intent_shadow'), provenance on live
            # recoveries. NULL on plain regex rows.
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS voice_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    body TEXT NOT NULL,
                    handler TEXT NOT NULL,
                    args TEXT,
                    label TEXT,
                    ok INTEGER NOT NULL DEFAULT 1,
                    error TEXT,
                    model_pred TEXT
                )
            """)
            va_cols = [r[1] for r in self.conn.execute(
                "PRAGMA table_info(voice_actions)").fetchall()]
            if "model_pred" not in va_cols:
                self.conn.execute(
                    "ALTER TABLE voice_actions ADD COLUMN model_pred TEXT")
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
        # Step 6: "My Voice" humanize feature (2026-07-20).
        # voice_samples — user-pasted writing samples that seed the humanize
        # pass's voice profile. A dashboard-managed collection, like
        # user_snippets; CRUD lives in src/dashboard/voice_samples.py.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS voice_samples (
                id INTEGER PRIMARY KEY,
                content TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'pasted',
                enabled INTEGER NOT NULL DEFAULT 1,
                char_len INTEGER NOT NULL,
                added_at REAL NOT NULL
            )
        """)
        # humanize_shadow — what the humanize pass WOULD have produced when
        # experimental.humanize == 'shadow', logged without changing the pasted
        # text so precision can be reviewed before trusting it. Stored plaintext
        # (the review UI needs the real text); log-file redaction is separate.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS humanize_shadow (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                dictation_id INTEGER,
                style TEXT,
                cleaned_text TEXT NOT NULL,
                humanized_text TEXT NOT NULL,
                similarity REAL,
                reviewed INTEGER NOT NULL DEFAULT 0
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_humanize_shadow_ts ON humanize_shadow(ts)"
        )
        self.conn.commit()
        # All single-threaded setup above is done on the raw connection. Now
        # wrap it so every subsequent access — from daemon threads AND the
        # dashboard's direct `history.conn.execute(...)` — serializes on one
        # lock. See _SafeConn for the concurrency rationale.
        self.conn = _SafeConn(self.conn)

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
                   error: str | None = None,
                   model_pred: str | None = None) -> int:
        """Append a row to voice_actions (Phase 14). Best-effort — caller
        swallows errors, mirroring log_command. ``model_pred`` is the intent
        model's JSON record for this utterance (MODEL-SHADOW), or None."""
        cur = self.conn.execute(
            "INSERT INTO voice_actions(ts, body, handler, args, label, ok, error, "
            "model_pred) VALUES (?,?,?,?,?,?,?,?)",
            (time.time(), body or "", handler, args_json, label,
             1 if ok else 0, error, model_pred),
        )
        self.conn.commit()
        return cur.lastrowid or 0

    def recent_actions(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, ts, body, handler, args, label, ok, error, model_pred "
            "FROM voice_actions ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [
            {"id": r[0], "ts": r[1], "body": r[2], "handler": r[3],
             "args": r[4], "label": r[5], "ok": bool(r[6]), "error": r[7],
             "model_pred": r[8]}
            for r in rows
        ]

    def intent_agreement_stats(self, days: int = 30) -> dict:
        """MODEL-SHADOW: aggregate the persisted intent-model records over the
        window into the three online measurements:

          hits      — executed regex commands the model also scored in shadow
                      mode: how often it agreed on the action (and the args).
          shadow    — regex misses where the model made a gated guess: how many
                      would actually have fired (vs. refused by the allowlist).
          recovered — live-mode executions the model (not the regex) produced,
                      and how many of those succeeded.

        Malformed records are skipped, never raised."""
        cutoff = time.time() - max(0, int(days)) * 86400
        hits = {"n": 0, "agree": 0, "args_match": 0}
        shadow = {"n": 0, "resolved": 0}
        recovered = {"n": 0, "ok": 0}
        rows = self.conn.execute(
            "SELECT handler, ok, model_pred FROM voice_actions "
            "WHERE model_pred IS NOT NULL AND ts >= ?", (cutoff,),
        ).fetchall()
        for handler, ok, raw in rows:
            try:
                rec = json.loads(raw)
                if not isinstance(rec, dict):
                    continue
            except (TypeError, ValueError):
                continue
            if handler == "intent_shadow":
                shadow["n"] += 1
                shadow["resolved"] += 1 if rec.get("resolved") else 0
            elif rec.get("recovered"):
                recovered["n"] += 1
                recovered["ok"] += 1 if ok else 0
            elif "agree" in rec:
                hits["n"] += 1
                hits["agree"] += 1 if rec.get("agree") else 0
                hits["args_match"] += 1 if rec.get("args_match") else 0
        return {"days": int(days), "hits": hits, "shadow": shadow,
                "recovered": recovered}

    # --- "My Voice" humanize shadow (dashboard-reviewed measurement) ----------

    def log_humanize_shadow(self, *, cleaned_text: str, humanized_text: str,
                            style: str | None = None,
                            dictation_id: int | None = None,
                            similarity: float | None = None) -> int:
        """Append what the humanize pass WOULD have produced (shadow mode).
        Best-effort — the caller swallows errors, mirroring log_action."""
        cur = self.conn.execute(
            "INSERT INTO humanize_shadow(ts, dictation_id, style, cleaned_text, "
            "humanized_text, similarity, reviewed) VALUES (?,?,?,?,?,?,0)",
            (time.time(), dictation_id, style, cleaned_text or "",
             humanized_text or "", similarity),
        )
        self.conn.commit()
        return cur.lastrowid or 0

    def recent_humanize_shadow(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, ts, dictation_id, style, cleaned_text, humanized_text, "
            "similarity, reviewed FROM humanize_shadow ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [
            {"id": r[0], "ts": r[1], "dictation_id": r[2], "style": r[3],
             "cleaned_text": r[4], "humanized_text": r[5], "similarity": r[6],
             "reviewed": bool(r[7])}
            for r in rows
        ]

    def humanize_shadow_stats(self, days: int = 30) -> dict:
        """Aggregate the shadow window: how many the meaning-guard would have
        ACCEPTED (a real change that stayed on-meaning) vs. how many produced no
        change, plus the average similarity — the online-precision signal the
        dashboard shows before the user trusts 'on'. Never raises."""
        cutoff = time.time() - max(0, int(days)) * 86400
        rows = self.conn.execute(
            "SELECT cleaned_text, humanized_text, similarity FROM humanize_shadow "
            "WHERE ts >= ?", (cutoff,),
        ).fetchall()
        n = changed = 0
        sims: list[float] = []
        for cleaned, humanized, sim in rows:
            n += 1
            if (humanized or "") != (cleaned or ""):
                changed += 1
            if sim is not None:
                sims.append(float(sim))
        avg_sim = round(sum(sims) / len(sims), 4) if sims else None
        return {"days": int(days), "n": n, "changed": changed,
                "avg_similarity": avg_sim}

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
        # `id` is a tiebreaker: items extracted in the same second share a
        # created_at, and without it SQLite's order among ties is unspecified
        # (the dashboard list would shuffle between requests). Newest first.
        return self.conn.execute(
            "SELECT id, dictation_id, text, created_at "
            "FROM action_items WHERE completed = 0 "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
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
