"""Assemble the user's "voice profile" for the humanize pass.

The profile is a compact block of *the user's own words* that the humanize
pass (src/cleanup.py Cleaner.humanize) conditions on so its rewrite reads like
the user. Two sources, both local:

  A. WRITING SAMPLES — text the user actually wrote and pasted into the
     dashboard (`voice_samples` table). The high-signal seed, since dictation
     history is mostly model output the user passively accepted.
  B. MINED EXEMPLARS — dictations the user actively shaped: rows they
     thumbs-approved (`user_rating = 1`) or corrected (`original_cleaned !=
     cleaned_text`). These are the scarce-but-genuine "how I phrase things"
     signal already in the DB.

The profile is intentionally style-independent: a person's writing voice does
not change with the focused app, so the same profile serves every style; the
per-style tone lives in the humanize system prompt, not here. Returns "" when
there is nothing to condition on, in which case the humanize pass is skipped
(no profile ⇒ no rewrite).

This is deliberately separate from Learner.build_prompt_augmentation
(src/learn.py:166): that augmentation teaches the *cleanup* how to
grammar-correct; this teaches the *humanize* pass how to sound like the user.
"""
from __future__ import annotations

import threading
import time

from .dashboard import voice_samples as _vs

# Styles the humanize pass applies to. Excludes 'code' (never reword code) and
# 'prompt' (PE mode owns its output). A person's voice belongs in prose.
HUMANIZE_STYLES = frozenset({"polished", "default", "casual", "email"})

# Budgets — keep the profile small so a local model stays fast and on-task.
SAMPLE_CHAR_BUDGET = 4000     # total across enabled writing samples
MAX_EXEMPLARS = 6             # mined dictation lines
MIN_EXEMPLAR_CHARS = 12       # skip trivially short lines
_CACHE_TTL = 60               # seconds; mirrors Learner.personal_vocabulary

_cache: "str | None" = None
_cache_ts = 0.0
_lock = threading.Lock()


def humanize_mode_for_cfg(exp: dict) -> str:
    """Normalize experimental.humanize to 'off' | 'on' | 'shadow'.

    Mirrors intent_model.backend_for_cfg: 'shadow' if the value is the shadow
    string, else 'on' when truthy, else 'off'. Tolerates the string forms the
    config/dashboard might store."""
    v = (exp or {}).get("humanize", False)
    if isinstance(v, str):
        s = v.strip().lower()
        if s == "shadow":
            return "shadow"
        return "on" if s in ("on", "true", "1", "yes") else "off"
    return "on" if v else "off"


def invalidate() -> None:
    """Drop the cached profile — call after a sample edit or a new dictation so
    fresh writing/exemplars take effect without a daemon restart."""
    global _cache, _cache_ts
    with _lock:
        _cache = None
        _cache_ts = 0.0


def _mined_exemplars(history) -> list[str]:
    """Dictations the user approved or corrected — their own phrasing. Newest
    first, deduped case-insensitively, capped. Never raises."""
    try:
        rows = history.conn.execute(
            "SELECT cleaned_text FROM dictations "
            "WHERE source != 'mobile' AND cleaned_text IS NOT NULL "
            "AND length(cleaned_text) >= ? "
            "AND (user_rating = 1 OR (original_cleaned IS NOT NULL "
            "     AND original_cleaned != cleaned_text)) "
            "ORDER BY ts DESC LIMIT ?",
            (MIN_EXEMPLAR_CHARS, MAX_EXEMPLARS * 4),
        ).fetchall()
    except Exception:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for (text,) in rows:
        t = (text or "").strip()
        key = t.lower()
        if not t or key in seen:
            continue
        seen.add(key)
        out.append(t)
        if len(out) >= MAX_EXEMPLARS:
            break
    return out


def _samples(history) -> list[str]:
    """Enabled writing samples, newest first, trimmed to the char budget."""
    try:
        texts = _vs.enabled_texts(history.conn)
    except Exception:
        return []
    out: list[str] = []
    used = 0
    for t in texts:
        t = (t or "").strip()
        if not t:
            continue
        if used + len(t) > SAMPLE_CHAR_BUDGET and out:
            break
        out.append(t[:SAMPLE_CHAR_BUDGET])
        used += len(t)
        if used >= SAMPLE_CHAR_BUDGET:
            break
    return out


def build(history, retriever=None, style: str = "polished") -> str:
    """Return the voice-profile block, or "" if there is nothing to learn from.

    Cached for 60s (the profile is small and read on every dictation). Pass the
    live History; `retriever`/`style` are accepted for call-site symmetry with
    the cleanup augmentation but the profile is style-independent.
    """
    global _cache, _cache_ts
    with _lock:
        if _cache is not None and (time.time() - _cache_ts) < _CACHE_TTL:
            return _cache

    profile = ""
    if history is not None:
        samples = _samples(history)
        exemplars = _mined_exemplars(history)
        parts: list[str] = []
        if samples:
            parts.append(
                "WRITING SAMPLES (how you actually write):\n"
                + "\n---\n".join(samples)
            )
        if exemplars:
            parts.append(
                "DICTATIONS YOU KEPT OR CORRECTED (your phrasing, your words):\n"
                + "\n".join(f"- {e}" for e in exemplars)
            )
        profile = "\n\n".join(parts)

    with _lock:
        _cache = profile
        _cache_ts = time.time()
    return profile
