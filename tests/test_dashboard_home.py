"""Phase 1 acceptance tests for the Home section.

Verifies analytics aggregation correctness AND end-to-end Home rendering:
- Total words, WPM (7-day), day-streak computations
- Mobile rows excluded from desktop-trust stats
- Mobile rows STILL visible in the history list (user can review them)
- Grouping into Today / Yesterday / Older labels
- Empty-state placeholder when history is empty
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import types

import pytest

from src.history import History


def _seed(history: History, *, ts: float, words: int,
          duration_ms: int = 6000, source: str = "desktop") -> int:
    """Insert a dictation at an explicit timestamp (bypasses time.time())."""
    text = " ".join(["word"] * words)
    cur = history.conn.execute(
        "INSERT INTO dictations(ts, window_title, style, language, duration_ms, "
        "raw_text, cleaned_text, source) VALUES (?, 'Test', 'default', 'en', ?, ?, ?, ?)",
        (ts, duration_ms, text, text, source),
    )
    history.conn.commit()
    return cur.lastrowid or 0


def _fresh_history(tmp_path) -> History:
    return History(str(tmp_path / "h.db"))


# --- total_words -------------------------------------------------------------

def test_total_words_sums_desktop_only_by_default(tmp_path):
    from src.dashboard.analytics import total_words
    h = _fresh_history(tmp_path)
    now = dt.datetime.now().timestamp()
    _seed(h, ts=now, words=10, source="desktop")
    _seed(h, ts=now, words=20, source="desktop")
    _seed(h, ts=now, words=100, source="mobile")  # excluded
    assert total_words(h.conn) == 30


def test_total_words_includes_mobile_when_asked(tmp_path):
    from src.dashboard.analytics import total_words
    h = _fresh_history(tmp_path)
    now = dt.datetime.now().timestamp()
    _seed(h, ts=now, words=10, source="desktop")
    _seed(h, ts=now, words=100, source="mobile")
    assert total_words(h.conn, include_mobile=True) == 110


# --- current_wpm -------------------------------------------------------------

def test_wpm_averages_per_dictation_rate(tmp_path):
    from src.dashboard.analytics import current_wpm
    h = _fresh_history(tmp_path)
    now = dt.datetime.now().timestamp()
    # 12 words in 6s = 120 wpm. Two identical entries should average to 120.
    _seed(h, ts=now, words=12, duration_ms=6000)
    _seed(h, ts=now, words=12, duration_ms=6000)
    assert current_wpm(h.conn) == 120


def test_wpm_excludes_too_short_dictations(tmp_path):
    from src.dashboard.analytics import current_wpm
    h = _fresh_history(tmp_path)
    now = dt.datetime.now().timestamp()
    _seed(h, ts=now, words=5, duration_ms=200)  # under floor — skipped
    _seed(h, ts=now, words=20, duration_ms=10000)  # 120 wpm
    assert current_wpm(h.conn) == 120


def test_wpm_ignores_dictations_outside_window(tmp_path):
    from src.dashboard.analytics import current_wpm
    h = _fresh_history(tmp_path)
    now = dt.datetime.now().timestamp()
    eight_days_ago = now - (8 * 86400)
    _seed(h, ts=eight_days_ago, words=200, duration_ms=10000)  # 1200 wpm — excluded
    _seed(h, ts=now, words=10, duration_ms=10000)  # 60 wpm
    assert current_wpm(h.conn, window_days=7) == 60


def test_wpm_zero_when_no_recent_data(tmp_path):
    from src.dashboard.analytics import current_wpm
    h = _fresh_history(tmp_path)
    assert current_wpm(h.conn) == 0


# --- day_streak --------------------------------------------------------------

def _ts_for(day: dt.date, hour: int = 12) -> float:
    return dt.datetime(day.year, day.month, day.day, hour).timestamp()


def test_streak_today_only_is_1(tmp_path):
    from src.dashboard.analytics import day_streak
    h = _fresh_history(tmp_path)
    _seed(h, ts=_ts_for(dt.date.today()), words=5)
    assert day_streak(h.conn) == 1


def test_streak_consecutive_days(tmp_path):
    from src.dashboard.analytics import day_streak
    h = _fresh_history(tmp_path)
    today = dt.date.today()
    for offset in range(5):
        _seed(h, ts=_ts_for(today - dt.timedelta(days=offset)), words=3)
    assert day_streak(h.conn) == 5


def test_streak_gap_breaks_count(tmp_path):
    from src.dashboard.analytics import day_streak
    h = _fresh_history(tmp_path)
    today = dt.date.today()
    _seed(h, ts=_ts_for(today), words=3)
    _seed(h, ts=_ts_for(today - dt.timedelta(days=1)), words=3)
    # Skip day-2, then day-3 + day-4 — those don't count.
    _seed(h, ts=_ts_for(today - dt.timedelta(days=3)), words=3)
    _seed(h, ts=_ts_for(today - dt.timedelta(days=4)), words=3)
    assert day_streak(h.conn) == 2


def test_streak_zero_when_nothing_today_or_yesterday(tmp_path):
    from src.dashboard.analytics import day_streak
    h = _fresh_history(tmp_path)
    _seed(h, ts=_ts_for(dt.date.today() - dt.timedelta(days=3)), words=5)
    assert day_streak(h.conn) == 0


def test_streak_yesterday_counts_grace_period(tmp_path):
    from src.dashboard.analytics import day_streak
    h = _fresh_history(tmp_path)
    yesterday = dt.date.today() - dt.timedelta(days=1)
    _seed(h, ts=_ts_for(yesterday), words=5)
    _seed(h, ts=_ts_for(yesterday - dt.timedelta(days=1)), words=5)
    # No dictation today, but yesterday counts. Streak = 2 from yesterday backward.
    assert day_streak(h.conn) == 2


# --- recent_grouped ----------------------------------------------------------

def test_grouped_today_yesterday_buckets(tmp_path):
    from src.dashboard.analytics import recent_grouped
    h = _fresh_history(tmp_path)
    today = dt.date.today()
    yesterday = today - dt.timedelta(days=1)
    _seed(h, ts=_ts_for(today, hour=10), words=4)
    _seed(h, ts=_ts_for(today, hour=15), words=6)
    _seed(h, ts=_ts_for(yesterday, hour=18), words=8)
    _seed(h, ts=_ts_for(today - dt.timedelta(days=5)), words=2)

    groups = recent_grouped(h.conn)
    labels = [g["group"] for g in groups]
    assert labels[0] == "Today"
    assert labels[1] == "Yesterday"
    # Today has 2 items, ordered newest first by ts DESC.
    assert len(groups[0]["items"]) == 2
    assert len(groups[1]["items"]) == 1


def test_grouped_includes_mobile_by_default(tmp_path):
    """Mobile rows show up in the history list — user wants to see them."""
    from src.dashboard.analytics import recent_grouped
    h = _fresh_history(tmp_path)
    now = dt.datetime.now().timestamp()
    _seed(h, ts=now, words=3, source="desktop")
    _seed(h, ts=now, words=3, source="mobile")
    groups = recent_grouped(h.conn)
    sources = {item["source"] for g in groups for item in g["items"]}
    assert sources == {"desktop", "mobile"}


# --- home_payload + Home route -----------------------------------------------

def _client_with_history(history):
    from src.dashboard.app import make_app
    app_ref = types.SimpleNamespace(
        cfg={"dashboard": {"host": "127.0.0.1", "port": 8766, "theme": "dark"}},
        history=history,
    )
    return make_app(app_ref).test_client()


def test_home_route_renders_real_dictations(tmp_path):
    # PR-C reshape: Home is now the Inbox surface. No more day grouping,
    # no "Welcome back" greeting, no WPM/streak (those moved to /insights).
    h = _fresh_history(tmp_path)
    _seed(h, ts=_ts_for(dt.date.today()), words=4)
    client = _client_with_history(h)
    r = client.get("/", headers={"Host": "127.0.0.1:8766"})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Inbox" in body
    assert "Approve" in body
    assert "Mark bad" in body
    assert "acceptance" in body


def test_home_route_shows_empty_state_when_no_history(tmp_path):
    h = _fresh_history(tmp_path)
    client = _client_with_history(h)
    r = client.get("/", headers={"Host": "127.0.0.1:8766"})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "No dictations yet" in body


def test_home_route_handles_missing_history():
    """If app_ref.history is None (cfg has history disabled), don't crash."""
    from src.dashboard.app import make_app
    app_ref = types.SimpleNamespace(
        cfg={"dashboard": {"host": "127.0.0.1", "port": 8766}},
        history=None,
    )
    client = make_app(app_ref).test_client()
    r = client.get("/", headers={"Host": "127.0.0.1:8766"})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # PR-C: inbox header shows even with no history backend.
    assert "Inbox" in body
    assert "No dictations yet" in body
