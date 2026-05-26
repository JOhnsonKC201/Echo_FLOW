"""Inbox view — Home page's active correction surface.

Replaces the passive grouped-history list. Each card surfaces a single
dictation with raw→cleaned diff and Approve / Mark-bad / Edit actions.

This module owns:
  - inbox_rows(conn, n) — recent dictations with the columns the card needs
  - render_diff(raw, cleaned) — list of (type, text) tuples for templates
"""
from __future__ import annotations

import difflib
import sqlite3
import time


def inbox_rows(conn: sqlite3.Connection, n: int = 15) -> list[dict]:
    """Return the last N dictations as a list of dicts with everything the
    template needs. Newest first."""
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
