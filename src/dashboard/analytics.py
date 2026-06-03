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

    Core counters (stable contract):
    - words_corrected: |word_count(cleaned_text) - word_count(raw_text)| summed.
      A rough but honest "Echo edited X words on your behalf."
    - dictionary_fixes: count of dictations where raw vs cleaned differ AND
      the raw word survives as a substring of the cleaned (treat as a vocab fix).
      Approximation — exact attribution requires per-token diff.
    - total: words_corrected + dictionary_fixes.

    Enrichment counters (for the premium breakdown card):
    - total_dictations: rows with both raw + cleaned text considered.
    - words_added / words_removed: the signed split of words_corrected
      (words_corrected == words_added + words_removed). Echo mostly *trims*
      filler and comma-storms, so words_removed usually dominates.
    - chars_corrected: |len(cleaned) - len(raw)| summed (character-level churn).
    - touch_rate: fraction of dictations Echo changed (dictionary_fixes / total).
    """
    where_src = _source_clause(include_mobile)
    cur = conn.execute(
        f"SELECT raw_text, cleaned_text FROM dictations "
        f"WHERE raw_text IS NOT NULL AND cleaned_text IS NOT NULL{where_src}"
    )
    words_corrected = 0
    dictionary_fixes = 0
    words_added = 0
    words_removed = 0
    chars_corrected = 0
    total_dictations = 0
    for raw, cleaned in cur:
        total_dictations += 1
        rw, cw = _word_count(raw), _word_count(cleaned)
        delta = cw - rw
        words_corrected += abs(delta)
        if delta > 0:
            words_added += delta
        elif delta < 0:
            words_removed += -delta
        chars_corrected += abs(len((cleaned or "").strip()) - len((raw or "").strip()))
        if raw.strip() != cleaned.strip():
            dictionary_fixes += 1
    touch_rate = (dictionary_fixes / total_dictations) if total_dictations else 0.0
    return {
        "words_corrected": words_corrected,
        "dictionary_fixes": dictionary_fixes,
        "total": words_corrected + dictionary_fixes,
        "total_dictations": total_dictations,
        "words_added": words_added,
        "words_removed": words_removed,
        "chars_corrected": chars_corrected,
        "touch_rate": touch_rate,
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
    # Snap the window start back to a Monday so every column is a clean Mon–Sun
    # week. Without this the first column is a partial week and, because cells are
    # placed by weekday alone, the grid never lines up into tidy columns.
    raw_start = today - dt.timedelta(days=weeks * 7 - 1)
    start = raw_start - dt.timedelta(days=raw_start.weekday())
    num_days = (today - start).days + 1
    days = []
    peak = max(counts.values()) if counts else 0
    for offset in range(num_days):
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
                     "weekday": d.weekday(), "week": offset // 7})
    num_weeks = (num_days + 6) // 7
    return {"days": days, "weeks": num_weeks, "max": peak}


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


def insights_payload(conn: sqlite3.Connection, *, include_mobile: bool = False) -> dict:
    """One call for the Insights route.

    `include_mobile` follows the same convention as the lower-level helpers:
    False (default) shows the desktop user's stats; True folds in mobile
    bridge entries. Used by the Outcomes "Desktop / Mobile / All" toggle.
    """
    return {
        "wpm": current_wpm(conn, include_mobile=include_mobile),
        "total_words": total_words(conn, include_mobile=include_mobile),
        "streak": day_streak(conn, include_mobile=include_mobile),
        "fixes": fixes_made(conn, include_mobile=include_mobile),
        "heatmap": streak_heatmap(conn, include_mobile=include_mobile),
        "apps": app_usage_breakdown(conn, include_mobile=include_mobile),
        "trend": quality_trend(conn, include_mobile=include_mobile),
    }


# -- Voice tab payload (PR-F) ----------------------------------------------

_FILLER_WORDS = frozenset({
    "um", "uh", "like", "actually", "basically", "literally",
})
# Bigrams kept separately so they can be matched without losing single-word
# context. "you know" needs to match the two-word sequence.
_FILLER_BIGRAMS = frozenset({("you", "know")})


def _tokenize_lower(text: str) -> list[str]:
    """Lowercase word-token split. Strips punctuation; keeps apostrophes."""
    import re
    return re.findall(r"[A-Za-z']+", (text or "").lower())


def _wpm_buckets(rates: list[float]) -> list[dict]:
    """Histogram with fixed buckets so the chart axis is stable across users."""
    edges = [0, 60, 80, 100, 120, 140, 160, 180, 220]
    labels = ["<60", "60-79", "80-99", "100-119", "120-139",
              "140-159", "160-179", "180-219", "220+"]
    counts = [0] * len(labels)
    for r in rates:
        placed = False
        for i in range(len(edges) - 1):
            if edges[i] <= r < edges[i + 1]:
                counts[i] += 1
                placed = True
                break
        if not placed:
            counts[-1] += 1
    return [{"label": labels[i], "count": counts[i]} for i in range(len(labels))]


def voice_payload(conn: sqlite3.Connection, *, days: int = 30) -> dict:
    """Stats for the "Your Voice" tab: pace, filler ratio, vocab, top bigrams.

    All computed from the last `days` of desktop dictations (mobile excluded —
    voice quality is a function of the user's mic+room, and the phone path
    masks both).
    """
    since = _now_ts() - (days * 86400)
    cur = conn.execute(
        "SELECT cleaned_text, duration_ms FROM dictations "
        "WHERE ts >= ? AND cleaned_text IS NOT NULL AND source = 'desktop'",
        (since,),
    )

    rates: list[float] = []
    all_tokens: list[str] = []
    filler_count = 0
    bigram_counts: dict[tuple[str, str], int] = defaultdict(int)

    for text, dur_ms in cur:
        toks = _tokenize_lower(text)
        if not toks:
            continue
        # WPM per dictation (same floor as current_wpm to suppress noise).
        if dur_ms and dur_ms > 0:
            seconds = dur_ms / 1000.0
            if seconds >= _MIN_DURATION_S_FOR_WPM:
                rates.append(len(toks) / (seconds / 60.0))
        # Filler-word count: single words.
        for t in toks:
            if t in _FILLER_WORDS:
                filler_count += 1
        # Filler bigrams.
        for i in range(len(toks) - 1):
            pair = (toks[i], toks[i + 1])
            if pair in _FILLER_BIGRAMS:
                filler_count += 1
            # All bigrams for "most-used phrases" (skip if either token is a
            # stopword-y filler so the chart shows meaning, not "of the").
            if pair[0] in _STOPWORDS or pair[1] in _STOPWORDS:
                continue
            bigram_counts[pair] += 1
        all_tokens.extend(toks)

    total = len(all_tokens)
    unique = len(set(all_tokens))
    filler_ratio = (filler_count / total) if total else 0.0
    vocab_diversity = (unique / total) if total else 0.0

    top_bigrams = sorted(bigram_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]
    phrases = [{"phrase": f"{a} {b}", "count": c} for (a, b), c in top_bigrams]

    return {
        "pace": {
            "median_wpm": int(round(sorted(rates)[len(rates) // 2])) if rates else 0,
            "buckets": _wpm_buckets(rates),
            "n": len(rates),
        },
        "filler": {
            "count": filler_count,
            "total_words": total,
            "ratio": filler_ratio,
            "ratio_pct": round(filler_ratio * 100, 2),
        },
        "vocabulary": {
            "unique": unique,
            "total": total,
            "diversity_pct": round(vocab_diversity * 100, 1),
        },
        "phrases": phrases,
        "days": days,
    }


# Tiny stopword list — just enough to keep "of the" off the chart without
# dragging in NLTK. Order-insensitive.
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at",
    "for", "with", "by", "is", "was", "are", "were", "be", "been", "it",
    "i", "you", "he", "she", "we", "they", "this", "that", "these", "those",
    "my", "your", "his", "her", "our", "their", "as", "if", "so", "do",
    "did", "does", "have", "has", "had", "will", "would", "can", "could",
    "should", "from", "not", "no", "yes",
})


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


# -- Senior rewrite outcome metrics (PR-C right column + PR-D Insights) ----

import time as _time


def _typing_wpm_baseline(conn: sqlite3.Connection, default: int = 40) -> int:
    """Average typing speed used to compute time-saved deltas. Defaults to
    40 WPM (decent typist) so the number isn't flattering."""
    return default


def time_saved_ms(conn: sqlite3.Connection, days: int = 30) -> int:
    """Estimated typing time saved over the last N days. Math:
        time_saved = words_dictated * (60s / typing_baseline_wpm)
                   - sum(dictation_duration_ms)

    Negative results clamp to 0 (degenerate: very short dictations where
    typing-baseline-equivalent < actual speaking time)."""
    since = _time.time() - (days * 86400)
    row = conn.execute(
        "SELECT COALESCE(SUM(LENGTH(cleaned_text) - LENGTH(REPLACE(cleaned_text, ' ', ''))), 0), "
        "       COALESCE(SUM(LENGTH(cleaned_text)), 0), "
        "       COALESCE(SUM(duration_ms), 0) "
        "FROM dictations WHERE ts >= ?",
        (since,),
    ).fetchone()
    spaces, char_count, total_dur_ms = row
    # Word count ≈ spaces + 1 per non-empty dictation. Cheaper than splitting
    # in Python and works inside SQL.
    n_rows = conn.execute(
        "SELECT COUNT(*) FROM dictations WHERE ts >= ? AND LENGTH(cleaned_text) > 0",
        (since,),
    ).fetchone()[0]
    words = spaces + n_rows
    if words <= 0:
        return 0
    baseline_wpm = _typing_wpm_baseline(conn)
    typing_equiv_ms = int(words * (60_000 / baseline_wpm))
    saved = typing_equiv_ms - int(total_dur_ms)
    return max(0, saved)


def acceptance_rate(conn: sqlite3.Connection, days: int = 7) -> dict:
    """% of dictations in the window whose cleaned_text == original_cleaned
    (i.e. user didn't open the editor to fix the model's output).

    Returns {current, prior, delta_pp, n_current, n_prior}. NULL ratings
    are treated as accepted-by-default — the user can mark them bad in
    the Inbox; a 'bad' rating overrides equality.
    """
    now = _time.time()
    cur_since = now - (days * 86400)
    prior_since = now - (2 * days * 86400)

    def _bucket(since: float, until: float) -> tuple[int, int]:
        total = conn.execute(
            "SELECT COUNT(*) FROM dictations WHERE ts >= ? AND ts < ? AND source = 'desktop'",
            (since, until),
        ).fetchone()[0]
        accepted = conn.execute(
            "SELECT COUNT(*) FROM dictations "
            "WHERE ts >= ? AND ts < ? AND source = 'desktop' "
            "  AND (user_rating IS NULL OR user_rating = 1) "
            "  AND (original_cleaned IS NULL OR original_cleaned = cleaned_text) "
            "  AND COALESCE(user_rating, 0) >= 0",
            (since, until),
        ).fetchone()[0]
        return accepted, total

    a_cur, n_cur = _bucket(cur_since, now)
    a_prev, n_prev = _bucket(prior_since, cur_since)
    cur_rate = (a_cur / n_cur) if n_cur else 0.0
    prev_rate = (a_prev / n_prev) if n_prev else 0.0
    delta_pp = (cur_rate - prev_rate) * 100
    return {
        "current": cur_rate,
        "prior": prev_rate,
        "delta_pp": delta_pp,
        "n_current": n_cur,
        "n_prior": n_prev,
    }


def latency_percentiles(conn: sqlite3.Connection, n: int = 200) -> dict:
    """p50 / p95 over the last N dictations that have latency_ms populated.

    Returns {p50, p95, n}. Empty result if there's no data yet (newly
    installed user, or all dictations predate the latency_ms column).
    """
    rows = conn.execute(
        "SELECT latency_ms FROM dictations "
        "WHERE latency_ms IS NOT NULL "
        "ORDER BY id DESC LIMIT ?",
        (int(n),),
    ).fetchall()
    samples = sorted(int(r[0]) for r in rows if r[0] is not None)
    if not samples:
        return {"p50": None, "p95": None, "n": 0}

    def _pct(p: float) -> int:
        idx = int(round((len(samples) - 1) * p))
        return samples[max(0, min(idx, len(samples) - 1))]

    return {"p50": _pct(0.50), "p95": _pct(0.95), "n": len(samples)}


def today_summary(conn: sqlite3.Connection) -> dict:
    """Compact summary for Home's right column.

    {count, time_saved_ms, acceptance_pct, latency_p95_ms}
    All values default to 0/None on empty/missing data — safe for templates.
    """
    midnight = _time.mktime(_time.struct_time(_time.localtime()[:3] + (0, 0, 0, 0, 0, -1)))
    count = conn.execute(
        "SELECT COUNT(*) FROM dictations WHERE ts >= ?", (midnight,)
    ).fetchone()[0]
    return {
        "count": int(count),
        "time_saved_ms": time_saved_ms(conn, days=1),
        "acceptance": acceptance_rate(conn, days=7),
        "latency": latency_percentiles(conn, n=200),
    }


def humanize_ms(ms: int) -> str:
    """Render a duration in ms as the most useful unit: ms, s, m, h.

    Used by both Home (right column) and Insights (time-saved tile).
    """
    if ms is None or ms <= 0:
        return "0 ms"
    ms = int(ms)
    if ms < 1000:
        return f"{ms} ms"
    s = ms / 1000
    if s < 60:
        return f"{s:.1f} s"
    m = s / 60
    if m < 60:
        return f"{m:.0f} m"
    h, rem_m = divmod(int(m), 60)
    if rem_m == 0:
        return f"{h}h"
    return f"{h}h {rem_m}m"
