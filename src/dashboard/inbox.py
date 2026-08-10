"""Inbox view — Home page's active correction surface.

Replaces the passive grouped-history list. Each card surfaces a single
dictation with raw→cleaned diff and Approve / Mark-bad / Edit actions.

This module owns:
  - inbox_rows(conn, n) — recent dictations with the columns the card needs
  - review_reasons(row) / needs_review(row) - which pile a card belongs in
  - render_diff(raw, cleaned) — list of (type, text) tuples for templates
"""
from __future__ import annotations

import difflib
import sqlite3
import time


def teacher_compare_rows(conn: sqlite3.Connection, n: int = 25) -> list[dict]:
    """Pair each recent teacher row with its source user dictation.

    Joins dictations on raw_text — for every source='teacher' row we find the
    most-recent non-teacher row with the same raw_text. Lets the dashboard
    show "you said X, you cleaned it Y, the teacher cleaned it Z" side-by-side
    so the user can audit (and eventually approve/reject) what the teacher
    is contributing to learning.
    """
    rows = conn.execute(
        """
        SELECT t.id, t.ts, t.raw_text, t.cleaned_text AS teacher_cleaned,
               (SELECT u.cleaned_text FROM dictations u
                  WHERE u.source != 'teacher' AND u.raw_text = t.raw_text
                  ORDER BY u.id DESC LIMIT 1) AS user_cleaned,
               (SELECT u.id FROM dictations u
                  WHERE u.source != 'teacher' AND u.raw_text = t.raw_text
                  ORDER BY u.id DESC LIMIT 1) AS user_id,
               t.style, t.quality_score
        FROM dictations t
        WHERE t.source = 'teacher'
        ORDER BY t.id DESC
        LIMIT ?
        """,
        (int(n),),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        out.append({
            "teacher_id": r[0],
            "ts": r[1] or 0.0,
            "ts_human": format_ts(r[1] or 0.0),
            "raw_text": r[2] or "",
            "teacher_cleaned": r[3] or "",
            "user_cleaned": r[4] or "",
            "user_id": r[5],
            "style": r[6] or "default",
            "quality_score": r[7],
            "differs_from_user": (r[3] or "") != (r[4] or "") if r[4] else True,
        })
    return out


# Quality below this reads as "worth a look". Not a new number: home.html has
# always coloured the q pill `good` at >= 75, so the UI already draws the line here.
LOW_QUALITY = 75
# At or under this many words, an utterance is short enough that a dropped clause
# would not be obvious from reading it.
SHORT_WORDS = 3
_TERMINAL = ('.', '!', '?', '"', ')', '…')


def review_reasons(row: dict) -> list[str]:
    """Why this dictation might want a human look. Empty list means it looks fine.

    Deliberately a *flag*, never a verdict: nothing in the app acts on this, it
    only decides which pile a card sits in. That asymmetry is why the checks are
    OR'd and lean toward flagging - a flagged good row costs one glance, while a
    missed bad row costs the correction signal entirely.

    Measured over the 975 real dictations in a live history.db, the three checks
    together catch 37 of 41 rows the user had marked bad (90%).

    NOT checked here, having been tested against that same data and rejected:
      - words-per-second. Looked decisive on a handful of recent bad rows and
        collapsed across the full set (median 1.89 for bad vs 2.05 for the rest),
        so any threshold that caught the bad rows also flagged half the good ones.
      - "is it a grammatically complete sentence". Completeness does not imply
        correctness: "Again." and "LinkedIn is." are both complete, both scored
        96+, and both were marked bad.
    """
    text = (row.get("cleaned_text") or "").strip()
    if not text:
        return []                      # nothing pasted; there is nothing to judge

    reasons: list[str] = []
    q = row.get("quality_score")
    if q is not None and q < LOW_QUALITY:
        reasons.append("low quality score")
    if len(text.split()) <= SHORT_WORDS:
        reasons.append("very short")
    if not text.endswith(_TERMINAL):
        reasons.append("looks cut off")
    return reasons


def needs_review(row: dict) -> bool:
    """Should this card sit in the visible pile rather than the collapsed one?

    Separate from review_reasons because the two answer different questions.
    review_reasons is what we *tell* the user, and a row they already acted on
    needs no explanation - the card is already wearing an "approved" or "marked
    bad" pill. But it must still stay visible, or acting on a card would make it
    vanish mid-interaction.
    """
    if row.get("user_rating") == -1:
        return True
    original = row.get("original_cleaned") or ""
    if original and original != (row.get("cleaned_text") or "").strip():
        return True
    return bool(review_reasons(row))


def inbox_rows(conn: sqlite3.Connection, n: int = 15) -> list[dict]:
    """Return the last N dictations as a list of dicts with everything the
    template needs. Newest first.

    Each row carries `reasons` (see review_reasons) so the template can split the
    list into "needs a look" and "looks fine" without re-deriving anything.
    """
    rows = conn.execute(
        """
        SELECT id, ts, window_title, style, language, source,
               raw_text, cleaned_text, original_cleaned,
               quality_score, user_rating, latency_ms
        FROM dictations
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(n),),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        out.append({
            "id": r[0],
            "ts": r[1] or 0.0,
            "window_title": r[2] or "",
            "style": r[3] or "default",
            "language": r[4] or "",
            "source": (r[5] or "desktop"),
            "raw_text": r[6] or "",
            "cleaned_text": r[7] or "",
            "original_cleaned": r[8] or "",
            "quality_score": r[9],
            "user_rating": r[10],
            "latency_ms": r[11],
        })
        out[-1]["reasons"] = review_reasons(out[-1])
        out[-1]["needs_review"] = needs_review(out[-1])
    return out


def render_diff(raw: str, cleaned: str) -> list[tuple[str, str]]:
    """Word-level diff between raw and cleaned. Returns a list of
    (kind, text) tuples where kind is one of 'add' | 'del' | 'eq'.

    The output is intentionally line-friendly — `add` and `del` tuples
    are emitted as opcode-grouped segments so the template can render
    them as inline pills inside two diff lines (one '-' raw, one '+'
    cleaned). For now, return both 'raw' and 'cleaned' compositions
    as a single flat list, in order:

        [('del-line-start', ''),
         ('eq'|'del', 'token '),  ...,
         ('add-line-start', ''),
         ('eq'|'add', 'token '),  ...]

    Templates render each tuple as a span and add a line break between
    the two line-start markers.
    """
    raw_tokens = (raw or "").split()
    cleaned_tokens = (cleaned or "").split()
    matcher = difflib.SequenceMatcher(a=raw_tokens, b=cleaned_tokens, autojunk=False)

    raw_line: list[tuple[str, str]] = []
    add_line: list[tuple[str, str]] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        a_slice = " ".join(raw_tokens[i1:i2])
        b_slice = " ".join(cleaned_tokens[j1:j2])
        if tag == "equal":
            if a_slice:
                raw_line.append(("eq", a_slice + " "))
            if b_slice:
                add_line.append(("eq", b_slice + " "))
        elif tag == "delete":
            if a_slice:
                raw_line.append(("del", a_slice + " "))
        elif tag == "insert":
            if b_slice:
                add_line.append(("add", b_slice + " "))
        elif tag == "replace":
            if a_slice:
                raw_line.append(("del", a_slice + " "))
            if b_slice:
                add_line.append(("add", b_slice + " "))

    result: list[tuple[str, str]] = []
    if raw_line:
        result.append(("del-line-start", ""))
        result.extend(raw_line)
    if add_line:
        result.append(("add-line-start", ""))
        result.extend(add_line)
    return result


def has_diff(raw: str, cleaned: str) -> bool:
    """Cheap test: is there any meaningful difference between raw and cleaned?"""
    return (raw or "").strip() != (cleaned or "").strip()


def format_ts(ts: float) -> str:
    """Human time for an inbox card."""
    if not ts:
        return ""
    try:
        return time.strftime("%b %d  %H:%M", time.localtime(float(ts)))
    except Exception:
        return ""
