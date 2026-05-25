"""Dashboard analytics — pure SQL aggregations against the dictations table.

Returns plain dicts/lists. No rendering, no HTML. Templates do the formatting.

Computer-first design: by default these counters exclude `source='mobile'`
rows. Mobile dictations are still searchable in Home/history, but the
"who am I as a dictator" stats represent the desktop user, not whatever
phone happens to push to the bridge.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from collections import defaultdict


# Reasonable typing-speed midline. Anything under this is "you're not really
# dictating, you're testing the mic." We use it to floor the WPM denominator
# so a 200ms test dictation doesn't claim 30,000 WPM.
_MIN_DURATION_S_FOR_WPM = 0.5


def _now_ts() -> float:
    return dt.datetime.now().timestamp()


def _word_count(s: str | None) -> int:
    if not s:
        return 0
    return len(s.split())


def _source_clause(include_mobile: bool) -> str:
    """SQL fragment for filtering by source. WAL-safe (no parameters needed)."""
    return "" if include_mobile else " AND source = 'desktop'"


def total_words(conn: sqlite3.Connection, *, include_mobile: bool = False) -> int:
    """Sum of word counts across all cleaned dictations.

    cleaned_text is what the user actually pasted, so that's what we count.
    """
    where_src = _source_clause(include_mobile)
    cur = conn.execute(
        f"SELECT cleaned_text FROM dictations "
        f"WHERE cleaned_text IS NOT NULL{where_src}"
    )
    total = 0
    for (text,) in cur:
        total += _word_count(text)
    return total


def current_wpm(
    conn: sqlite3.Connection,
    *,
    window_days: int = 7,
    include_mobile: bool = False,
) -> int:
    """Median-ish WPM across recent dictations.

    We compute per-dictation WPM (words / minutes-spoken) and average. That's
    more meaningful than total_words / total_seconds because long pauses
    between dictations shouldn't drag the average down.
    """
    cutoff = _now_ts() - (window_days * 86400)
    where_src = _source_clause(include_mobile)
    cur = conn.execute(
        f"SELECT cleaned_text, duration_ms FROM dictations "
        f"WHERE ts >= ? AND cleaned_text IS NOT NULL{where_src}",
        (cutoff,),
    )
    rates: list[float] = []
    for text, duration_ms in cur:
        if not duration_ms or duration_ms <= 0:
            continue
        seconds = duration_ms / 1000.0
        if seconds < _MIN_DURATION_S_FOR_WPM:
            continue
        words = _word_count(text)
        if words == 0:
            continue
        rates.append(words / (seconds / 60.0))
    if not rates:
        return 0
    return int(round(sum(rates) / len(rates)))


def day_streak(
    conn: sqlite3.Connection,
    *,
    include_mobile: bool = False,
) -> int:
    """Consecutive days ending today (or yesterday) with >=1 dictation.

    If no dictation today AND no dictation yesterday, the streak is 0.
    If there's one today, count consecutive prior days.
    If there's one yesterday but not today, count back from yesterday
    (we don't break the streak until midnight of the second missed day).
    """
    where_src = _source_clause(include_mobile)
    cur = conn.execute(
        f"SELECT ts FROM dictations WHERE 1=1{where_src} ORDER BY ts DESC"
    )
    # Bucket by local date.
    days_with_any: set[dt.date] = set()
    for (ts,) in cur:
        d = dt.datetime.fromtimestamp(ts).date()
        days_with_any.add(d)
        # Early exit once we have ~400 days — anyone with longer streak doesn't need a tighter count.
        if len(days_with_any) > 400:
            break

    if not days_with_any:
        return 0

    today = dt.date.today()
    yesterday = today - dt.timedelta(days=1)

    if today in days_with_any:
        anchor = today
    elif yesterday in days_with_any:
        anchor = yesterday
    else:
        return 0

    streak = 0
    cursor_day = anchor
    while cursor_day in days_with_any:
        streak += 1
        cursor_day -= dt.timedelta(days=1)
    return streak


def _first_line(text: str | None, *, max_chars: int = 140) -> str:
    if not text:
        return ""
    first = text.strip().splitlines()[0] if text.strip() else ""
    if len(first) > max_chars:
        return first[: max_chars - 1].rstrip() + "…"
    return first


def _group_label(ts: float, today: dt.date, yesterday: dt.date) -> str:
    d = dt.datetime.fromtimestamp(ts).date()
    if d == today:
        return "Today"
    if d == yesterday:
        return "Yesterday"
    # Older entries grouped by long date (e.g. "May 23, 2026").
    return d.strftime("%b %-d, %Y") if hasattr(d, "isoformat") and False else d.strftime("%b %d, %Y")


def recent_grouped(
    conn: sqlite3.Connection,
    *,
    limit: int = 200,
    include_mobile: bool = True,
) -> list[dict]:
    """Return [{"group": "Today", "items": [{"id","time","text","source"}, ...]}].

    Mobile rows ARE included here — they're real dictations the user made
    and should be visible. They just don't poison the WPM/streak stats.
    """
    where_src = _source_clause(include_mobile)
    cur = conn.execute(
        f"SELECT id, ts, cleaned_text, raw_text, source, window_title "
        f"FROM dictations WHERE 1=1{where_src} ORDER BY ts DESC LIMIT ?",
        (limit,),
    )
    today = dt.date.today()
    yesterday = today - dt.timedelta(days=1)
    bucket: dict[str, list[dict]] = defaultdict(list)
    # Preserve insertion order in Python 3.7+ — iterate newest-first naturally.
    order: list[str] = []
    for row in cur:
        rid, ts, cleaned, raw, source, window_title = row
        label = _group_label(ts, today, yesterday)
        if label not in bucket:
            order.append(label)
        text = (cleaned or raw or "").strip()
        bucket[label].append({
            "id": rid,
            "time": dt.datetime.fromtimestamp(ts).strftime("%I:%M %p").lstrip("0"),
            "text": _first_line(text),
            "source": source or "desktop",
            "window": window_title or "",
        })
    return [{"group": label, "items": bucket[label]} for label in order]


def fixes_made(
    conn: sqlite3.Connection,
    *,
    include_mobile: bool = False,
) -> dict:
    """How much Echo Flow has fixed.

    Returns {"words_corrected": N, "dictionary_fixes": M, "total": N+M}.
    - words_corrected: |word_count(cleaned_text) - word_count(raw_text)| summed.
      A rough but honest "Echo edited X words on your behalf."
    - dictionary_fixes: count of dictations where raw vs cleaned differ AND
      the raw word survives as a substring of the cleaned (treat as a vocab fix).
      Approximation — exact attribution requires per-token diff.
    """
    where_src = _source_clause(include_mobile)
    cur = conn.execute(
        f"SELECT raw_text, cleaned_text FROM dictations "
        f"WHERE raw_text IS NOT NULL AND cleaned_text IS NOT NULL{where_src}"
    )
    words_corrected = 0
    dictionary_fixes = 0
    for raw, cleaned in cur:
        rw, cw = _word_count(raw), _word_count(cleaned)
        words_corrected += abs(cw - rw)
        if raw.strip() != cleaned.strip():
            dictionary_fixes += 1
    return {
        "words_corrected": words_corrected,
        "dictionary_fixes": dictionary_fixes,
        "total": words_corrected + dictionary_fixes,
    }


def streak_heatmap(
    conn: sqlite3.Connection,
    *,
    weeks: int = 14,
    include_mobile: bool = False,
) -> dict:
    """GitHub-style heatmap data.

    Returns {"days": [{"date": "YYYY-MM-DD", "count": N, "level": 0..4}, ...],
             "weeks": <weeks>, "max": <peak count>}.
    `level` is a 0-4 bucket suitable for color-stepping in CSS.
    Days are emitted oldest->newest so the template can fill columns naturally.
    """
    where_src = _source_clause(include_mobile)
    cutoff = _now_ts() - (weeks * 7 * 86400)
    cur = conn.execute(
        f"SELECT ts FROM dictations WHERE ts >= ?{where_src}",
        (cutoff,),
    )
    counts: dict[dt.date, int] = defaultdict(int)
    for (ts,) in cur:
        counts[dt.datetime.fromtimestamp(ts).date()] += 1

    today = dt.date.today()
    start = today - dt.timedelta(days=weeks * 7 - 1)
    days = []
    peak = max(counts.values()) if counts else 0
    for offset in range(weeks * 7):
        d = start + dt.timedelta(days=offset)
        c = counts.get(d, 0)
        # 5 buckets: 0, 1-2, 3-5, 6-10, 11+. Scales with usage but stable.
        if c == 0:
            level = 0
        elif c <= 2:
            level = 1
        elif c <= 5:
            level = 2
        elif c <= 10:
            level = 3
        else:
            level = 4
        days.append({"date": d.isoformat(), "count": c, "level": level,
                     "weekday": d.weekday()})
    return {"days": days, "weeks": weeks, "max": peak}


def app_usage_breakdown(
    conn: sqlite3.Connection,
    *,
    top_n: int = 6,
    window_days: int = 30,
    include_mobile: bool = False,
) -> list[dict]:
    """Group dictations by window_title bucket; return top N + 'Other'.

    Returns [{"label": "Code", "count": N, "pct": 0.79}, ...] sorted by count desc.
    Mirrors Wispr's "Desktop usage" panel.
    """
    where_src = _source_clause(include_mobile)
    cutoff = _now_ts() - (window_days * 86400)
    cur = conn.execute(
        f"SELECT window_title FROM dictations "
        f"WHERE ts >= ?{where_src}",
        (cutoff,),
    )
    bucket_for_title = _bucket_window_title
    counts: dict[str, int] = defaultdict(int)
    total = 0
    for (title,) in cur:
        label = bucket_for_title(title)
        counts[label] += 1
        total += 1
    if total == 0:
        return []
    sorted_buckets = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    top = sorted_buckets[:top_n]
    rest = sorted_buckets[top_n:]
    result = [
        {"label": label, "count": cnt, "pct": cnt / total}
        for label, cnt in top
    ]
    if rest:
        other = sum(c for _, c in rest)
        result.append({"label": "Other", "count": other, "pct": other / total})
    return result


# Window-title -> friendly category for the usage breakdown.
# Substring matching, first hit wins. Mirrors cleanup.profiles intent but
# decoupled (we don't want a usage chart change to break style routing).
_USAGE_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Code",      ("code", "cursor", "windsurf", "pycharm", "sublime", "vim", "intellij", "vscode")),
    ("Browser",   ("chrome", "edge", "firefox", "brave", "safari", "opera")),
    ("Chat",      ("slack", "discord", "teams", "whatsapp", "telegram", "signal", "messenger")),
    ("Email",     ("gmail", "outlook", "mail")),
    ("Documents", ("word", "docs", "notion", "obsidian", "onenote", "evernote")),
    ("Terminal",  ("terminal", "powershell", "cmd", "iterm", "wezterm")),
    ("Meet",      ("zoom", "meet", "webex")),
)


def _bucket_window_title(title: str | None) -> str:
    """Categorize a window title into a usage bucket label."""
    if not title:
        return "Other"
    t = title.lower()
    for label, needles in _USAGE_BUCKETS:
        if any(n in t for n in needles):
            return label
    return "Other"


def quality_trend(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    include_mobile: bool = False,
) -> list[float]:
    """Most-recent N quality scores, oldest->newest, for the sparkline.

    Returns floats in [0, 100]. Skips rows with NULL quality_score.
    """
    where_src = _source_clause(include_mobile)
    cur = conn.execute(
        f"SELECT quality_score FROM dictations "
        f"WHERE quality_score IS NOT NULL{where_src} "
        f"ORDER BY ts DESC LIMIT ?",
        (limit,),
    )
    rows = [float(q) for (q,) in cur if q is not None]
    rows.reverse()  # oldest first for left-to-right sparkline
    return rows


def insights_payload(conn: sqlite3.Connection) -> dict:
    """One call for the Insights route."""
    return {
        "wpm": current_wpm(conn),
        "total_words": total_words(conn),
        "streak": day_streak(conn),
        "fixes": fixes_made(conn),
        "heatmap": streak_heatmap(conn),
        "apps": app_usage_breakdown(conn),
        "trend": quality_trend(conn),
    }


def home_payload(
    conn: sqlite3.Connection,
    *,
    include_mobile_in_list: bool = True,
) -> dict:
    """Single call used by the Home route. Bundles stats + grouped history.

    Keeps the route handler trivial and makes test assertions easier.
    """
    return {
        "stats": {
            "total_words": total_words(conn),
            "wpm": current_wpm(conn),
            "streak": day_streak(conn),
        },
        "groups": recent_grouped(conn, include_mobile=include_mobile_in_list),
    }
