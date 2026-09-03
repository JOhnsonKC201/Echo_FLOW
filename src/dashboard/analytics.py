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

# Top of the words-per-minute axis. Pinned, not data-derived: a tile whose
# scale rescales with the data cannot be read at a glance, because a mark
# moving right could mean you sped up or the axis shrank.
_WPM_SCALE_MAX = 240


def _now_ts() -> float:
    return dt.datetime.now().timestamp()


def _word_count(s: str | None) -> int:
    if not s:
        return 0
    return len(s.split())


def _source_clause(include_mobile: "bool | str") -> str:
    """SQL fragment for filtering by source. WAL-safe (no parameters needed).

    Accepts either the historical boolean (False = desktop only, True = every
    source) or an explicit source name: "desktop" | "mobile" | "all". The
    boolean has no way to say "mobile only", which is why the Outcomes page's
    Mobile filter used to return desktop and mobile combined, byte-identical
    to All. Every helper forwards its `include_mobile` argument here unchanged,
    so passing a string through them selects a single source.
    """
    if isinstance(include_mobile, str):
        source = include_mobile.lower()
        if source == "mobile":
            return " AND source = 'mobile'"
        if source == "desktop":
            return " AND source = 'desktop'"
        return ""                      # "all"
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
    """Mean WPM across recent dictations.

    We compute per-dictation WPM (words / minutes-spoken) and take the
    arithmetic mean. That's more meaningful than total_words / total_seconds
    because long pauses between dictations shouldn't drag the average down.
    """
    rates = _wpm_rates(conn, window_days=window_days, include_mobile=include_mobile)
    if not rates:
        return 0
    return int(round(sum(rates) / len(rates)))


def _wpm_rates(
    conn: sqlite3.Connection,
    *,
    window_days: int = 7,
    include_mobile: "bool | str" = False,
) -> list[float]:
    """Per-dictation words-per-minute samples in the window.

    Split out of current_wpm so the headline number and the distribution drawn
    under it are computed from exactly the same samples, including the same
    duration floor and the same zero-word skip.
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
    return rates


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


def _activity_level(count: int) -> int:
    """0-4 bucket for a day's dictation count: 0, 1-2, 3-5, 6-10, 11+.

    One definition, because the 15 week grid and the 14 day strip in the
    streak tile render the same colour ramp and have to agree about what a
    given shade means.
    """
    if count == 0:
        return 0
    if count <= 2:
        return 1
    if count <= 5:
        return 2
    if count <= 10:
        return 3
    return 4


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
    today = dt.date.today()
    # Snap the window start back to a Monday so every column is a clean Mon-Sun
    # week. Without this the first column is a partial week and, because cells are
    # placed by weekday alone, the grid never lines up into tidy columns.
    raw_start = today - dt.timedelta(days=weeks * 7 - 1)
    start = raw_start - dt.timedelta(days=raw_start.weekday())
    # Derive the query cutoff FROM the grid, not from the clock. A wall-clock
    # `now - weeks*7*86400` always lands after `start` (which was just snapped
    # backwards to a Monday), so the leading cells were rendered from days the
    # query never fetched: real dictations there showed as level 0, and `peak`
    # was understated by the same rows. The gap is weekday-dependent, up to a
    # whole blank first column on a Saturday.
    cutoff = dt.datetime.combine(start, dt.time.min).timestamp()
    cur = conn.execute(
        f"SELECT ts FROM dictations WHERE ts >= ?{where_src}",
        (cutoff,),
    )
    counts: dict[dt.date, int] = defaultdict(int)
    for (ts,) in cur:
        counts[dt.datetime.fromtimestamp(ts).date()] += 1
    num_days = (today - start).days + 1
    days = []
    peak = max(counts.values()) if counts else 0
    for offset in range(num_days):
        d = start + dt.timedelta(days=offset)
        c = counts.get(d, 0)
        level = _activity_level(c)
        days.append({"date": d.isoformat(), "count": c, "level": level,
                     "weekday": d.weekday(), "week": offset // 7})
    num_weeks = (num_days + 6) // 7
    # Column index where each month starts, for the axis above the grid.
    # A month owning fewer than two columns is dropped: one column is about as
    # wide as a cell, so a one-column label would overlap the next one.
    months: list[dict] = []
    for d in days:
        if d["weekday"] != 0:
            continue
        label = dt.date.fromisoformat(d["date"]).strftime("%b")
        if not months or months[-1]["label"] != label:
            months.append({"label": label, "week": d["week"]})
    months = [
        m for i, m in enumerate(months)
        if ((months[i + 1]["week"] if i + 1 < len(months) else num_weeks)
            - m["week"]) >= 2
    ]
    return {"days": days, "weeks": num_weeks, "max": peak,
            "months": months,
            "active": sum(1 for d in days if d["count"] > 0),
            "span": num_days}


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


def insights_payload(conn: sqlite3.Connection, *,
                     include_mobile: "bool | str" = False,
                     apps_window_days: int = 30,
                     wpm_window_days: int = 7) -> dict:
    """One call for the Insights route.

    `include_mobile` follows the same convention as the lower-level helpers:
    False (default) shows the desktop user's stats; True folds in mobile
    bridge entries. Used by the Outcomes "Desktop / Mobile / All" toggle.
    """
    pace = wpm_profile(conn, window_days=wpm_window_days,
                       include_mobile=include_mobile)
    return {
        # Same object, so the number on the tile and the mark on its axis can
        # never disagree.
        "wpm": pace["mean"],
        "pace": pace,
        "total_words": total_words(conn, include_mobile=include_mobile),
        "streak": day_streak(conn, include_mobile=include_mobile),
        "fixes": fixes_made(conn, include_mobile=include_mobile),
        "heatmap": streak_heatmap(conn, include_mobile=include_mobile),
        "apps": app_usage_breakdown(conn, include_mobile=include_mobile,
                                    window_days=apps_window_days),
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
    # The open-ended top bucket is given the same 20 wpm width as its
    # neighbours so it can be drawn on a linear axis at all.
    bounds = list(zip(edges, edges[1:] + [_WPM_SCALE_MAX]))
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
    return [{"label": labels[i], "count": counts[i],
             "lo": bounds[i][0], "hi": bounds[i][1]}
            for i in range(len(labels))]


def voice_payload(conn: sqlite3.Connection, *, days: int = 30) -> dict:
    """Stats for the "Your Voice" tab: pace, filler ratio, vocab, top bigrams.

    All computed from the last `days` of desktop dictations (mobile excluded —
    voice quality is a function of the user's mic+room, and the phone path
    masks both).
    """
    since = _now_ts() - (days * 86400)
    cur = conn.execute(
        "SELECT cleaned_text, duration_ms, raw_text FROM dictations "
        "WHERE ts >= ? AND cleaned_text IS NOT NULL AND source = 'desktop'",
        (since,),
    )

    rates: list[float] = []
    all_tokens: list[str] = []
    filler_count = 0
    filler_total = 0
    bigram_counts: dict[tuple[str, str], int] = defaultdict(int)

    for text, dur_ms, raw_text in cur:
        toks = _tokenize_lower(text)
        if not toks:
            continue
        # Fillers are counted over the RAW transcript, because removing exactly
        # these tokens is the cleanup layer's job. Counting them in
        # cleaned_text measured the cleaner, not the speaker, so the tile could
        # only ever approach 0% no matter how the user actually talks.
        raw_toks = _tokenize_lower(raw_text) or toks
        filler_total += len(raw_toks)
        for t in raw_toks:
            if t in _FILLER_WORDS:
                filler_count += 1
        for i in range(len(raw_toks) - 1):
            if (raw_toks[i], raw_toks[i + 1]) in _FILLER_BIGRAMS:
                filler_count += 1
        # WPM per dictation (same floor as current_wpm to suppress noise).
        if dur_ms and dur_ms > 0:
            seconds = dur_ms / 1000.0
            if seconds >= _MIN_DURATION_S_FOR_WPM:
                rates.append(len(toks) / (seconds / 60.0))
        for i in range(len(toks) - 1):
            pair = (toks[i], toks[i + 1])
            # All bigrams for "most-used phrases" (skip if either token is a
            # stopword-y filler so the chart shows meaning, not "of the").
            if pair[0] in _STOPWORDS or pair[1] in _STOPWORDS:
                continue
            bigram_counts[pair] += 1
        all_tokens.extend(toks)

    total = len(all_tokens)
    unique = len(set(all_tokens))
    filler_ratio = (filler_count / filler_total) if filler_total else 0.0
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
            "total_words": filler_total,
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


def time_saved_ms(conn: sqlite3.Connection, days: int = 30, *,
                  since: float | None = None,
                  include_mobile: "bool | str" = False) -> int:
    """Estimated typing time saved over the last N days. Math:
        time_saved = words_dictated * (60s / typing_baseline_wpm)
                   - sum(dictation_duration_ms)

    Negative results clamp to 0 (degenerate: very short dictations where
    typing-baseline-equivalent < actual speaking time)."""
    # `since` lets a caller align this with a calendar boundary instead of a
    # rolling window. Home showed "0 dictations today" (midnight-based) beside
    # "9 m saved today" (now - 24h), which is a straight contradiction every
    # morning.
    if since is None:
        since = _time.time() - (days * 86400)
    # Teacher distillation writes a SECOND row for the same utterance with the
    # same duration_ms, and the mobile bridge writes rows the Insights page
    # deliberately excludes. Without this filter both tiles double-count.
    where_src = _source_clause(include_mobile)
    # Count words with the same definition total_words uses. The old
    # count-the-spaces SQL treated newlines and tabs as non-separators, so a
    # multi-paragraph email or a bulleted list read ~12-30% short, and the two
    # tiles disagreed about the same rows.
    rows = conn.execute(
        f"SELECT cleaned_text, duration_ms FROM dictations WHERE ts >= ?{where_src}",
        (since,),
    ).fetchall()
    words = 0
    total_dur_ms = 0
    for text, duration_ms in rows:
        words += _word_count(text)
        total_dur_ms += duration_ms or 0
    if words <= 0:
        return 0
    baseline_wpm = _typing_wpm_baseline(conn)
    typing_equiv_ms = int(words * (60_000 / baseline_wpm))
    saved = typing_equiv_ms - int(total_dur_ms)
    return max(0, saved)


# What counts as "the user kept it". Written once because two callers need
# to agree: the headline rate and the per-day series drawn beneath it. When
# these drifted apart the tile showed a percentage its own chart contradicted.
_KEPT_PREDICATE = (
    " AND (user_rating IS NULL OR user_rating = 1)"
    " AND (original_cleaned IS NULL OR original_cleaned = cleaned_text)"
    " AND COALESCE(user_rating, 0) >= 0"
)


def acceptance_rate(conn: sqlite3.Connection, days: int = 7, *,
                    include_mobile: "bool | str" = False) -> dict:
    """% of dictations in the window whose cleaned_text == original_cleaned
    (i.e. user didn't open the editor to fix the model's output).

    Returns {current, prior, delta_pp, n_current, n_prior}. NULL ratings
    are treated as accepted-by-default — the user can mark them bad in
    the Inbox; a 'bad' rating overrides equality.
    """
    now = _time.time()
    cur_since = now - (days * 86400)
    prior_since = now - (2 * days * 86400)

    where_src = _source_clause(include_mobile)

    def _bucket(since: float, until: float) -> tuple[int, int]:
        total = conn.execute(
            f"SELECT COUNT(*) FROM dictations WHERE ts >= ? AND ts < ?{where_src}",
            (since, until),
        ).fetchone()[0]
        accepted = conn.execute(
            f"SELECT COUNT(*) FROM dictations "
            f"WHERE ts >= ? AND ts < ?{where_src}{_KEPT_PREDICATE}",
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


def latency_percentiles(conn: sqlite3.Connection, n: int = 200, *,
                        include_mobile: "bool | str" = False) -> dict:
    """p50 / p95 over the last N dictations that have latency_ms populated.

    Returns {p50, p95, n}. Empty result if there's no data yet (newly
    installed user, or all dictations predate the latency_ms column).
    """
    where_src = _source_clause(include_mobile)
    rows = conn.execute(
        f"SELECT latency_ms FROM dictations "
        f"WHERE latency_ms IS NOT NULL{where_src} "
        f"ORDER BY id DESC LIMIT ?",
        (int(n),),
    ).fetchall()
    samples = sorted(int(r[0]) for r in rows if r[0] is not None)
    if not samples:
        return {"p50": None, "p95": None, "n": 0}

    def _pct(p: float) -> int:
        idx = int(round((len(samples) - 1) * p))
        return samples[max(0, min(idx, len(samples) - 1))]

    return {"p50": _pct(0.50), "p95": _pct(0.95), "n": len(samples)}


def duration_parts(ms: int) -> dict:
    """Split a duration into its number and its unit.

    humanize_ms glues the two together ("43 m"), which is right for prose but
    wrong for a stat tile: the unit renders at the value's size, and the
    count-up animation in app.js walks the whole string. Splitting them lets
    the unit take its own smaller type and keeps the animated text node
    purely numeric. Unit words, not single letters, so "43 m" cannot be read
    as metres.
    """
    ms = int(ms or 0)
    if ms <= 0:
        return {"value": "0", "unit": "min", "ms": 0}
    if ms < 1000:
        return {"value": str(ms), "unit": "ms", "ms": ms}
    seconds = ms / 1000
    if seconds < 60:
        return {"value": f"{seconds:.1f}", "unit": "sec", "ms": ms}
    minutes = seconds / 60
    if minutes < 60:
        return {"value": f"{minutes:.0f}", "unit": "min", "ms": ms}
    return {"value": f"{minutes / 60:.1f}", "unit": "hr", "ms": ms}


def daily_metrics(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    include_mobile: "bool | str" = False,
) -> list[dict]:
    """One row per calendar day, oldest first, with empty days filled in.

    Feeds the chart under each tile. The zero fill is the point: a series that
    silently omits quiet days compresses time, so a fortnight off renders as a
    continuous run and every chart drawn from it lies about pace.

    `saved_ms` is deliberately NOT clamped per day. time_saved_ms sums words
    and durations across the whole window and clamps once at the end, so
    clamping here would make the chart and the headline disagree on any day
    the user spoke slower than they type. Clamp at draw time instead.
    """
    where_src = _source_clause(include_mobile)
    today = dt.date.today()
    start = today - dt.timedelta(days=days - 1)
    cutoff = dt.datetime.combine(start, dt.time.min).timestamp()
    baseline = _typing_wpm_baseline(conn)
    per_day: dict[dt.date, dict] = {}
    rows = conn.execute(
        f"SELECT ts, cleaned_text, duration_ms FROM dictations "
        f"WHERE ts >= ?{where_src}",
        (cutoff,),
    )
    for ts, text, duration_ms in rows:
        day = dt.datetime.fromtimestamp(ts).date()
        bucket = per_day.setdefault(
            day, {"dictations": 0, "words": 0, "duration_ms": 0})
        bucket["dictations"] += 1
        bucket["words"] += _word_count(text)
        bucket["duration_ms"] += duration_ms or 0
    out = []
    for offset in range(days):
        day = start + dt.timedelta(days=offset)
        b = per_day.get(day) or {"dictations": 0, "words": 0, "duration_ms": 0}
        typing_equiv_ms = int(b["words"] * (60_000 / baseline))
        out.append({
            "date": day.isoformat(),
            "level": _activity_level(b["dictations"]),
            "dictations": b["dictations"],
            "words": b["words"],
            "duration_ms": b["duration_ms"],
            "saved_ms": typing_equiv_ms - int(b["duration_ms"]),
        })
    return out


def kept_daily(
    conn: sqlite3.Connection,
    *,
    days: int = 14,
    include_mobile: "bool | str" = False,
) -> list[dict]:
    """Per day: dictations kept as written vs edited. Oldest first, zero filled.

    A per-day *rate* would be the obvious series here and it would be the wrong
    one: on a three dictation day one edit swings it 33 points. Counts keep the
    sample size visible.
    """
    where_src = _source_clause(include_mobile)
    today = dt.date.today()
    start = today - dt.timedelta(days=days - 1)
    cutoff = dt.datetime.combine(start, dt.time.min).timestamp()
    totals: dict[dt.date, int] = defaultdict(int)
    kept: dict[dt.date, int] = defaultdict(int)
    for (ts,) in conn.execute(
        f"SELECT ts FROM dictations WHERE ts >= ?{where_src}", (cutoff,)
    ):
        totals[dt.datetime.fromtimestamp(ts).date()] += 1
    for (ts,) in conn.execute(
        f"SELECT ts FROM dictations WHERE ts >= ?{where_src}{_KEPT_PREDICATE}",
        (cutoff,),
    ):
        kept[dt.datetime.fromtimestamp(ts).date()] += 1
    out = []
    for offset in range(days):
        day = start + dt.timedelta(days=offset)
        total = totals.get(day, 0)
        out.append({"date": day.isoformat(), "total": total,
                    "kept": kept.get(day, 0), "edited": total - kept.get(day, 0)})
    return out


def latency_histogram(
    conn: sqlite3.Connection,
    *,
    n: int = 200,
    bins: int = 14,
    include_mobile: "bool | str" = False,
) -> dict:
    """Shape of the last N response times, with p50 and p95 located on the axis.

    The axis stops at p95 and everything slower folds into the final bin. A
    linear axis out to the true maximum would let one 40 second outlier squash
    the other 199 samples into the first bin, which is how a histogram ends up
    saying nothing at all.
    """
    where_src = _source_clause(include_mobile)
    rows = conn.execute(
        f"SELECT latency_ms FROM dictations "
        f"WHERE latency_ms IS NOT NULL{where_src} "
        f"ORDER BY id DESC LIMIT ?",
        (int(n),),
    ).fetchall()
    samples = sorted(int(r[0]) for r in rows if r[0] is not None)
    if not samples:
        return {"bins": [], "p50": None, "p95": None, "n": 0,
                "axis_max": 0, "over": 0, "p50_pos": 0.0, "p95_pos": 0.0}

    def _pct(q: float) -> int:
        idx = int(round((len(samples) - 1) * q))
        return samples[max(0, min(idx, len(samples) - 1))]

    p50, p95 = _pct(0.50), _pct(0.95)
    axis_max = max(p95, 1)
    width = axis_max / bins
    counts = [0] * bins
    over = 0
    for value in samples:
        if value > axis_max:
            over += 1
            counts[-1] += 1
            continue
        idx = min(int(value / width), bins - 1)
        counts[idx] += 1
    return {
        "bins": [{"lo": int(i * width), "hi": int((i + 1) * width),
                  "count": counts[i]} for i in range(bins)],
        "p50": p50, "p95": p95, "n": len(samples),
        "axis_max": axis_max, "over": over,
        "p50_pos": min(p50 / axis_max, 1.0),
        "p95_pos": min(p95 / axis_max, 1.0),
    }


def wpm_profile(
    conn: sqlite3.Connection,
    *,
    window_days: int = 7,
    include_mobile: "bool | str" = False,
) -> dict:
    """Mean WPM plus the distribution it came from, on a fixed axis.

    The axis is pinned to 0..220 rather than scaled to the data so the tile
    means the same thing between visits: a mark that moves right is you
    speaking faster, not the chart rescaling under you.
    """
    rates = _wpm_rates(conn, window_days=window_days, include_mobile=include_mobile)
    scale_max = _WPM_SCALE_MAX
    baseline = _typing_wpm_baseline(conn)
    mean = int(round(sum(rates) / len(rates))) if rates else 0
    ordered = sorted(rates)
    median = int(round(ordered[len(ordered) // 2])) if ordered else 0
    return {
        "mean": mean,
        "median": median,
        "n": len(rates),
        "buckets": _wpm_buckets(rates),
        "scale_max": scale_max,
        "baseline": baseline,
        "mean_pos": min(mean / scale_max, 1.0),
        "baseline_pos": min(baseline / scale_max, 1.0),
    }


def today_summary(conn: sqlite3.Connection) -> dict:
    """Compact summary for Home's right column.

    {count, time_saved_ms, acceptance_pct, latency_p95_ms}
    All values default to 0/None on empty/missing data — safe for templates.
    """
    midnight = _time.mktime(_time.struct_time(_time.localtime()[:3] + (0, 0, 0, 0, 0, -1)))
    # Both tiles must agree on what "today" means AND on what counts as a
    # dictation: teacher-distillation rows duplicate a real utterance, and the
    # acceptance tile beside these already filters to desktop.
    count = conn.execute(
        f"SELECT COUNT(*) FROM dictations WHERE ts >= ?{_source_clause(False)}",
        (midnight,),
    ).fetchone()[0]
    return {
        "count": int(count),
        "time_saved_ms": time_saved_ms(conn, since=midnight),
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
