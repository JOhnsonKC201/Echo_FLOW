"""Adaptive learning: builds a personalized cleanup prompt from your history.

Also mines (raw → cleaned) diff pairs into reusable token-level substitution
patterns stored in the `learned_patterns` table. These patterns power the
"learned" cleanup provider (LLM-free mode).

Strategy: every dictation goes into SQLite as (raw, cleaned) pairs. Before each
new cleanup, we pull the most recent meaningful pairs and inject them as
few-shot examples + a personal vocabulary list. The model learns YOUR
grammar mistakes, YOUR jargon, YOUR names — automatically, no training needed.

Why this works:
- LLMs are few-shot learners. 5-10 examples of YOUR raw->cleaned style
  is worth more than fine-tuning for a single user.
- Personal vocab catches names/terms Whisper mishears the same way every time
  ("Johnson" -> "Jonson" -> "Johnson" once it's in the glossary).
- Grammar patterns ("I am go to store" -> "I am going to the store") get
  reinforced because the model sees its own past corrections.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from collections import Counter
from contextlib import closing
from dataclasses import dataclass


# Cache vocabulary so we don't recount on every dictation. The cache holds the
# FULL ranked list (callers slice [:limit]); a lock guards reads/writes since
# the daemon and the dashboard/bridge threads both touch these globals.
_vocab_cache: list[str] | None = None
_vocab_cache_ts: float = 0.0
_vocab_cache_lock = threading.Lock()

# Casing canon — {word_lowercase: CanonicalForm}, learned from the user's
# in-app corrections (e.g. "tiktok" -> "TikTok"). Cached 60s like the vocab
# list; touched by the daemon and the editor/dashboard threads.
_casing_cache: dict[str, str] | None = None
_casing_cache_ts: float = 0.0
_casing_cache_lock = threading.Lock()


def _invalidate_casing_cache() -> None:
    """Drop the casing-canon cache so a fresh edit applies on the next dictation."""
    global _casing_cache, _casing_cache_ts
    with _casing_cache_lock:
        _casing_cache = None
        _casing_cache_ts = 0.0


@dataclass
class LearningConfig:
    enabled: bool = True
    max_examples: int = 6
    max_vocab_terms: int = 25
    min_example_chars: int = 12
    # Mirrors RetrievalConfig.trust_mobile. When False (default), mobile-bridge
    # dictations are excluded from few-shot examples and personal vocabulary so
    # untrusted LAN traffic cannot pollute the desktop's learned prompts.
    trust_mobile: bool = False
    # When True (default), teacher-distilled cleanups (source='teacher') are
    # eligible as few-shot examples and personal-vocab sources. The whole
    # point of the teacher layer is to learn from a stronger model, so unlike
    # `trust_mobile` this defaults ON — flip OFF to fall back to user-only
    # learning while still recording teacher rows for review.
    trust_teacher: bool = True


class Learner:
    def __init__(self, db_path: str, cfg: LearningConfig, retriever=None):
        self.db_path = db_path
        self.cfg = cfg
        self.retriever = retriever   # optional Retriever for semantic search

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def recent_examples(self, style: str, limit: int) -> list[tuple[str, str]]:
        """Pull (raw, cleaned) pairs that differ enough to be instructive."""
        if not self.cfg.enabled:
            return []
        excluded = []
        if not self.cfg.trust_mobile:
            excluded.append("'mobile'")
        if not self.cfg.trust_teacher:
            excluded.append("'teacher'")
        source_filter = f" AND source NOT IN ({','.join(excluded)})" if excluded else ""
        try:
            with closing(self._conn()) as conn:
                rows = conn.execute(
                    "SELECT raw_text, cleaned_text FROM dictations "
                    "WHERE style = ? AND length(raw_text) >= ? "
                    "AND raw_text != cleaned_text"
                    + source_filter +
                    " ORDER BY ts DESC LIMIT ?",
                    (style, self.cfg.min_example_chars, limit * 3),
                ).fetchall()
        except Exception:
            return []
        # Prefer pairs where the model actually changed something meaningful
        scored = []
        for raw, cleaned in rows:
            if not raw or not cleaned:
                continue
            diff = abs(len(cleaned) - len(raw)) + sum(
                1 for a, b in zip(raw, cleaned) if a != b
            )
            scored.append((diff, raw, cleaned))
        scored.sort(reverse=True, key=lambda x: x[0])
        return [(r, c) for _, r, c in scored[:limit]]

    def personal_vocabulary(self, limit: int) -> list[str]:
        """Frequent proper nouns / unusual terms from cleaned history."""
        global _vocab_cache, _vocab_cache_ts
        import time
        # Cache for 60s to avoid re-scanning on every dictation. The cache
        # stores the full ranked list, so slicing [:limit] is correct for any
        # caller's limit (a smaller limit populating the cache first must not
        # truncate a later larger-limit request).
        with _vocab_cache_lock:
            if _vocab_cache is not None and (time.time() - _vocab_cache_ts) < 60:
                return _vocab_cache[:limit]
        excluded = []
        if not self.cfg.trust_mobile:
            excluded.append("'mobile'")
        if not self.cfg.trust_teacher:
            excluded.append("'teacher'")
        source_filter = f" WHERE source NOT IN ({','.join(excluded)})" if excluded else ""
        try:
            with closing(self._conn()) as conn:
                texts = [row[0] or "" for row in conn.execute(
                    "SELECT cleaned_text FROM dictations"
                    + source_filter +
                    " ORDER BY ts DESC LIMIT 500"
                ).fetchall()]
        except Exception:
            return []
        counter: Counter[str] = Counter()
        # Capture: Capitalized words, CamelCase, snake_case, all-caps acronyms
        pattern = re.compile(r"\b([A-Z][a-z]{2,}|[A-Z]{2,}|[a-z]+_[a-z_]+|[a-z]+[A-Z][a-zA-Z]+)\b")
        STOP = {
            "The", "This", "That", "These", "Those", "There", "Then", "They",
            "Their", "When", "Where", "What", "Which", "While", "Who", "Why",
            "How", "And", "But", "Also", "From", "With", "Have", "Has", "Had",
            "Will", "Would", "Could", "Should", "Just", "Like", "About", "Into",
            "Over", "Under", "After", "Before", "Today", "Tomorrow", "Yesterday",
            "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
            "Sunday", "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        }
        for t in texts:
            for match in pattern.findall(t):
                if match in STOP or len(match) < 3:
                    continue
                counter[match] += 1
        # Need to appear at least twice to be a "personal" term. Cache the FULL
        # ranked list (no limit cap) so any caller's limit slices correctly.
        ranked = [w for w, c in counter.most_common() if c >= 2]
        with _vocab_cache_lock:
            _vocab_cache = ranked
            _vocab_cache_ts = time.time()
        return ranked[:limit]

    def build_prompt_augmentation(self, style: str, query_text: str = "") -> str:
        """Returns a string to append to the base system prompt.

        If a retriever is attached and query_text is provided, examples are
        chosen by SEMANTIC SIMILARITY to the current dictation (RAG).
        Otherwise falls back to most-recent examples.
        """
        if not self.cfg.enabled:
            return ""
        parts = []
        vocab = self.personal_vocabulary(self.cfg.max_vocab_terms)
        if vocab:
            parts.append(
                "USER VOCABULARY (preserve exact spelling/casing — these are names "
                "and terms the user uses often):\n"
                + ", ".join(vocab)
            )

        examples: list[tuple[str, str]] = []
        used_retrieval = False
        if self.retriever and query_text:
            results = self.retriever.search(query_text, style=style)
            if results:
                examples = [(r, c) for r, c, _sim in results]
                used_retrieval = True
        if not examples:
            examples = self.recent_examples(style, self.cfg.max_examples)

        if examples:
            ex_str = "\n\n".join(
                f"RAW: {r}\nCLEANED: {c}" for r, c in examples
            )
            label = (
                "SEMANTICALLY SIMILAR PAST DICTATIONS (use these as your "
                "ground truth for this user's grammar and style):"
                if used_retrieval else
                "RECENT EXAMPLES OF HOW THIS USER'S SPEECH SHOULD BE CLEANED:"
            )
            parts.append(label + "\n" + ex_str)
        if not parts:
            return ""
        return "\n\n---\n\n" + "\n\n".join(parts)

    def invalidate_cache(self):
        global _vocab_cache
        with _vocab_cache_lock:
            _vocab_cache = None


# --- Pattern mining for the LLM-free "learned" cleanup provider ---

# Patterns are stored as: (trigger_token_lc, replacement_token, success, total).
# We treat a "pattern" as a single-token substitution that consistently
# appears in (raw → cleaned) diffs for the same surrounding context-free token.
# This is intentionally simple — it covers the bulk of repeated transcription
# mishears (proper nouns, ESL contractions) without needing an LLM.

_TOKEN_RE = re.compile(r"\S+|\s+")


def _ensure_patterns_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS learned_patterns (
            trigger TEXT NOT NULL,
            replacement TEXT NOT NULL,
            success INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (trigger, replacement)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_patterns_trigger ON learned_patterns(trigger)"
    )
    # Origin attribution (additive). Lets the dashboard surface which
    # patterns came from the user vs. the teacher LLM, and lets us
    # selectively wipe teacher-only patterns if they go sideways.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(learned_patterns)").fetchall()}
    if "user_count" not in cols:
        conn.execute("ALTER TABLE learned_patterns ADD COLUMN user_count INTEGER NOT NULL DEFAULT 0")
    if "teacher_count" not in cols:
        conn.execute("ALTER TABLE learned_patterns ADD COLUMN teacher_count INTEGER NOT NULL DEFAULT 0")


def _ensure_casing_table(conn: sqlite3.Connection) -> None:
    """Per-word canonical-casing store, taught by user edits (tiktok -> TikTok).

    Kept separate from learned_patterns so it doesn't perturb the
    success/total confidence stats and can be loaded on its own cheap query
    for both the casing applier and the de-Title-Case allowlist.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS casing_canon (
            word_lc TEXT PRIMARY KEY,
            canonical TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        )
        """
    )


def _meaningful_casing(word: str) -> bool:
    """True when `word` carries casing worth remembering (not plain lowercase).

    Accepts leading-capital ("Johnson"), internal caps ("TikTok", "iPhone"),
    and all-caps ("SQL"). Rejects empty / all-lowercase / non-alphabetic forms.
    """
    # Strip apostrophes for the probe and allow digits so product/version names
    # survive ("iOS17", "PostgreSQL15", "GPT4"). Other punctuation still fails
    # isalnum, so trailing-punctuation artifacts ("word.") are rejected. Cover
    # the curly glyphs (’ ‘) too — Whisper emits U+2019, and stripping only the
    # ASCII ' left "TikTok’s" non-alnum, silently dropping the learned casing.
    core = word
    for _ap in ("'", "’", "‘"):
        core = core.replace(_ap, "")
    if len(core) < 2 or not core.isalnum() or not any(c.isalpha() for c in core):
        return False
    return word != word.lower()


def _tokenize(s: str) -> list[str]:
    # Split into word/whitespace tokens. Casing preserved on replacement; trigger lowercased.
    return [t for t in re.split(r"(\s+)", s) if t != ""]


def _diff_token_pairs(raw: str, cleaned: str) -> list[tuple[str, str]]:
    """Extract single-token substitutions between raw and cleaned text.

    Uses difflib SequenceMatcher on word tokens. Returns pairs where both
    sides have exactly one non-whitespace token (the 1↔1 substitutions).
    Multi-token replacements are skipped — too noisy for an LLM-free fix.
    """
    import difflib
    raw_toks = [t for t in _tokenize(raw) if not t.isspace()]
    cln_toks = [t for t in _tokenize(cleaned) if not t.isspace()]
    sm = difflib.SequenceMatcher(a=raw_toks, b=cln_toks, autojunk=False)
    pairs: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "replace" and (i2 - i1) == 1 and (j2 - j1) == 1:
            a, b = raw_toks[i1], cln_toks[j1]
            if a.lower() == b.lower():
                continue  # casing-only — let title-case rules handle it
            if len(a) < 2 or len(b) < 2:
                continue
            pairs.append((a, b))
    return pairs


def _diff_casing_pairs(before: str, after: str) -> list[tuple[str, str]]:
    """Extract casing-only 1↔1 token changes (e.g. "tiktok" → "TikTok").

    The complement of `_diff_token_pairs`, which deliberately skips these.
    Both sides are the same word ignoring case; only `after`'s casing differs
    and is meaningful (a capital somewhere). Used to learn a casing canon from
    the user's in-app corrections.
    """
    import difflib
    a_toks = [t for t in _tokenize(before) if not t.isspace()]
    b_toks = [t for t in _tokenize(after) if not t.isspace()]
    sm = difflib.SequenceMatcher(a=a_toks, b=b_toks, autojunk=False)
    pairs: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "replace" and (i2 - i1) == 1 and (j2 - j1) == 1:
            a, b = a_toks[i1], b_toks[j1]
            if a.lower() != b.lower():
                continue  # different word — handled by _diff_token_pairs
            if a == b or not _meaningful_casing(b):
                continue  # no change, or target has no casing worth keeping
            pairs.append((a, b))
    return pairs


class PatternMiner:
    """Mines (raw, cleaned) pairs into learned token substitutions."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def record(self, raw: str, cleaned: str, source: str = "user") -> int:
        """Increment success/total counts for every 1↔1 substitution observed.

        `source` is "user" for desktop/mobile dictations (where the cleanup is
        what actually got pasted) and "teacher" for background-distilled
        cleanups from a stronger model. Both contribute to confidence equally;
        the breakdown is tracked in user_count / teacher_count for auditing.

        Returns count of pairs recorded. Called after every dictation.
        """
        if not raw or not cleaned or raw == cleaned:
            return 0
        pairs = _diff_token_pairs(raw, cleaned)
        if not pairs:
            return 0
        import time as _t
        now = _t.time()
        with closing(self._conn()) as conn:
            _ensure_patterns_table(conn)
            is_teacher = (source == "teacher")
            for a, b in pairs:
                trigger = a.lower()
                # success = how often THIS replacement was the cleaned form
                conn.execute(
                    """
                    INSERT INTO learned_patterns
                        (trigger, replacement, success, total, updated_at, user_count, teacher_count)
                    VALUES (?, ?, 1, 1, ?, ?, ?)
                    ON CONFLICT(trigger, replacement) DO UPDATE SET
                        success = success + 1,
                        total = total + 1,
                        updated_at = excluded.updated_at,
                        user_count = user_count + ?,
                        teacher_count = teacher_count + ?
                    """,
                    (trigger, b, now, 0 if is_teacher else 1, 1 if is_teacher else 0,
                     0 if is_teacher else 1, 1 if is_teacher else 0),
                )
                # All OTHER replacements for the same trigger see total++ but not success.
                conn.execute(
                    "UPDATE learned_patterns SET total = total + 1, updated_at = ? "
                    "WHERE trigger = ? AND replacement != ?",
                    (now, trigger, b),
                )
            conn.commit()
        return len(pairs)

    def confident_patterns(self, min_confidence: float = 0.7, min_total: int = 2) -> dict[str, str]:
        """Return {trigger_lowercase: replacement} for patterns above threshold."""
        try:
            with closing(self._conn()) as conn:
                _ensure_patterns_table(conn)
                rows = conn.execute(
                    "SELECT trigger, replacement, success, total FROM learned_patterns "
                    "WHERE total >= ?",
                    (min_total,),
                ).fetchall()
        except Exception:
            return {}
        # For each trigger, pick the highest-confidence replacement above threshold.
        best: dict[str, tuple[str, float]] = {}
        for trig, repl, succ, total in rows:
            if total == 0:
                continue
            conf = succ / total
            if conf < min_confidence:
                continue
            cur = best.get(trig)
            if cur is None or conf > cur[1]:
                best[trig] = (repl, conf)
        return {t: r for t, (r, _) in best.items()}

    # ---- Casing canon (tiktok -> TikTok), learned from user edits ----------

    def record_casing(self, before: str, after: str) -> int:
        """Learn canonical casings from a single user edit. Returns pairs stored.

        `before`/`after` are the pre-edit and corrected text. Only casing-only
        1↔1 token changes are captured (different-word changes flow through
        `record`). A single edit is enough to canonicalize a word — the user
        explicitly chose edit-once-apply-forever.
        """
        if not before or not after:
            return 0
        pairs = _diff_casing_pairs(before, after)
        if not pairs:
            return 0
        import time as _t
        now = _t.time()
        with closing(self._conn()) as conn:
            _ensure_casing_table(conn)
            for _a, b in pairs:
                conn.execute(
                    """
                    INSERT INTO casing_canon (word_lc, canonical, count, updated_at)
                    VALUES (?, ?, 1, ?)
                    ON CONFLICT(word_lc) DO UPDATE SET
                        canonical = excluded.canonical,
                        count = count + 1,
                        updated_at = excluded.updated_at
                    """,
                    (b.lower(), b, now),
                )
            conn.commit()
        _invalidate_casing_cache()
        return len(pairs)

    def add_casing(self, canonical: str) -> str | None:
        """Teach a casing directly (dashboard path), bypassing the Fix dialog.

        `canonical` is a single word with the desired casing (e.g. "TikTok").
        Re-adding an existing word overrides its canonical form, so this also
        serves as the edit control. Returns the stored form, or None when the
        input isn't a single token carrying meaningful (non-all-lowercase)
        casing — the same bar `record_casing` applies to learned edits.
        """
        word = (canonical or "").strip()
        # Teach the BASE word: "London's" -> "London". The apply path strips the
        # possessive before lookup, so storing "london's" would be a dead row.
        mp = re.match(r"^(.+?)('[sS]|')$", word)
        if mp:
            word = mp.group(1)
        # Server-side length cap — the form's maxlength is client-only and a
        # direct POST can bypass it; an oversized canon entry would run on every
        # dictation's protected set + regex sub.
        if (not word or len(word) > 80 or len(word.split()) != 1
                or not _meaningful_casing(word)):
            return None
        import time as _t
        now = _t.time()
        with closing(self._conn()) as conn:
            _ensure_casing_table(conn)
            conn.execute(
                """
                INSERT INTO casing_canon (word_lc, canonical, count, updated_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(word_lc) DO UPDATE SET
                    canonical = excluded.canonical,
                    -- A re-add with the SAME form is reinforcement (+1); a
                    -- different casing is a corrective edit, not a reinforce,
                    -- so the "Reinforced N times" badge stays honest.
                    count = CASE WHEN canonical = excluded.canonical
                                 THEN count + 1 ELSE count END,
                    updated_at = excluded.updated_at
                """,
                (word.lower(), word, now),
            )
            conn.commit()
        _invalidate_casing_cache()
        return word

    def canonical_casings(self) -> dict[str, str]:
        """Return {word_lowercase: CanonicalForm}. Cached 60s (process-wide)."""
        global _casing_cache, _casing_cache_ts
        import time as _t
        with _casing_cache_lock:
            if _casing_cache is not None and (_t.time() - _casing_cache_ts) < 60:
                return _casing_cache
        try:
            with closing(self._conn()) as conn:
                _ensure_casing_table(conn)
                rows = conn.execute(
                    "SELECT word_lc, canonical FROM casing_canon WHERE count >= 1"
                ).fetchall()
        except Exception:
            return {}
        out = {str(w): str(c) for w, c in rows}
        with _casing_cache_lock:
            _casing_cache = out
            _casing_cache_ts = _t.time()
        return out

    def list_casings(self) -> list[dict]:
        """All canon entries for the dashboard: [{word_lc, canonical, count}]."""
        try:
            with closing(self._conn()) as conn:
                _ensure_casing_table(conn)
                rows = conn.execute(
                    "SELECT word_lc, canonical, count FROM casing_canon "
                    "ORDER BY canonical COLLATE NOCASE"
                ).fetchall()
        except Exception:
            return []
        return [{"word_lc": str(w), "canonical": str(c), "count": int(round(n or 0))}
                for w, c, n in rows]

    def delete_casing(self, word_lc: str) -> bool:
        """Remove one canon entry by its lowercase key. Returns True if removed."""
        key = (word_lc or "").strip().lower()
        if not key:
            return False
        try:
            with closing(self._conn()) as conn:
                _ensure_casing_table(conn)
                cur = conn.execute("DELETE FROM casing_canon WHERE word_lc = ?", (key,))
                conn.commit()
                removed = (cur.rowcount or 0) > 0
        except Exception:
            return False
        if removed:
            _invalidate_casing_cache()
        return removed

    def decay_stale(self, half_life_days: float = 14.0) -> tuple[int, int]:
        """Apply exponential time-decay to all learned_patterns rows.

        Each row's `success` and `total` get multiplied by
            factor = 0.5 ** (days_since_updated / half_life_days)
        Rows whose `total` falls below 0.5 are deleted (effectively forgotten).
        Then `updated_at` is set to now so the decay doesn't re-apply on next call.

        Returns (rows_decayed, rows_deleted).
        """
        try:
            with closing(self._conn()) as conn:
                _ensure_patterns_table(conn)
                import time as _t
                now = _t.time()
                # Count first for the return value.
                decayed = conn.execute("SELECT COUNT(*) FROM learned_patterns").fetchone()[0]
                conn.execute(
                    "UPDATE learned_patterns SET "
                    "  success = success * pow(0.5, (? - updated_at) / (? * 86400.0)),"
                    "  total   = total   * pow(0.5, (? - updated_at) / (? * 86400.0)),"
                    "  updated_at = ? ",
                    (now, half_life_days, now, half_life_days, now),
                )
                cur = conn.execute("DELETE FROM learned_patterns WHERE total < 0.5")
                deleted = cur.rowcount or 0
                # Casing canon decays on the same schedule so corrections the
                # user stops making eventually fade (slower: a half-count floor
                # keeps a once-taught proper noun alive far longer than a noisy
                # substitution).
                _ensure_casing_table(conn)
                conn.execute(
                    "UPDATE casing_canon SET "
                    "  count = count * pow(0.5, (? - updated_at) / (? * 86400.0)),"
                    "  updated_at = ? ",
                    (now, half_life_days, now),
                )
                conn.execute("DELETE FROM casing_canon WHERE count < 0.25")
                conn.commit()
                _invalidate_casing_cache()
                return int(decayed), int(deleted)
        except Exception:
            return 0, 0

    def backfill_from_history(self, limit: int = 5000) -> int:
        """Mine patterns from the existing dictations table. Returns rows processed."""
        try:
            with closing(self._conn()) as conn:
                _ensure_patterns_table(conn)
                rows = conn.execute(
                    "SELECT raw_text, cleaned_text FROM dictations "
                    "WHERE raw_text != cleaned_text AND raw_text != '' AND cleaned_text != '' "
                    "ORDER BY ts DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        except Exception:
            return 0
        n = 0
        for raw, cleaned in rows:
            n += self.record(raw, cleaned)
        return n

    def backfill_casings_from_history(self, limit: int = 5000) -> int:
        """One-shot: mine casing-only user edits into the casing canon.

        The signal is the user's correction: original_cleaned (model output)
        vs cleaned_text (what the user saved). Casing-only changes there mean
        "I fixed this word's capitalization". Guarded by an app_meta flag so it
        runs exactly once — never resurrecting a casing the user later deletes
        in the dashboard. Returns pairs recorded.
        """
        flag = "casing_backfill_v1_done"
        try:
            with closing(self._conn()) as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT)"
                )
                if conn.execute("SELECT 1 FROM app_meta WHERE key = ?", (flag,)).fetchone():
                    return 0
                cols = {r[1] for r in conn.execute("PRAGMA table_info(dictations)").fetchall()}
                rows = []
                if "original_cleaned" in cols:
                    rows = conn.execute(
                        "SELECT original_cleaned, cleaned_text FROM dictations "
                        "WHERE original_cleaned IS NOT NULL AND cleaned_text IS NOT NULL "
                        "AND original_cleaned != cleaned_text "
                        "ORDER BY ts DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
        except Exception:
            return 0
        n = 0
        for before, after in rows:
            n += self.record_casing(before or "", after or "")
        # Mark done regardless of count so we never re-scan on later startups.
        try:
            with closing(self._conn()) as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT)"
                )
                conn.execute(
                    "INSERT OR REPLACE INTO app_meta(key, value) VALUES(?, ?)", (flag, "1")
                )
                conn.commit()
        except Exception:
            pass
        return n
