"""Phase 2 acceptance tests — Insights analytics + render."""
from __future__ import annotations

import datetime as dt
import types

import pytest

from src.history import History


def _seed(history: History, *, ts: float, raw: str, cleaned: str | None = None,
          duration_ms: int = 6000, window_title: str = "Test - Notepad",
          source: str = "desktop", quality: float | None = None) -> int:
    cleaned = raw if cleaned is None else cleaned
    cur = history.conn.execute(
        "INSERT INTO dictations(ts, window_title, style, language, duration_ms, "
        "raw_text, cleaned_text, source, quality_score) "
        "VALUES (?, ?, 'default', 'en', ?, ?, ?, ?, ?)",
        (ts, window_title, duration_ms, raw, cleaned, source, quality),
    )
    history.conn.commit()
    return cur.lastrowid or 0


def _h(tmp_path) -> History:
    return History(str(tmp_path / "h.db"))


def _ts(day: dt.date, hour: int = 12) -> float:
    return dt.datetime(day.year, day.month, day.day, hour).timestamp()


# --- fixes_made --------------------------------------------------------------

def test_fixes_counts_word_delta(tmp_path):
    from src.dashboard.analytics import fixes_made
    h = _h(tmp_path)
    now = dt.datetime.now().timestamp()
    _seed(h, ts=now, raw="hi there", cleaned="Hi, there!")  # 2 words both -> delta 0, but text differs
    _seed(h, ts=now, raw="um hello", cleaned="Hello.")  # 2 -> 1 = delta 1
    f = fixes_made(h.conn)
    assert f["words_corrected"] == 1
    assert f["dictionary_fixes"] == 2  # both rows changed text
    assert f["total"] == 3


def test_fixes_excludes_mobile_by_default(tmp_path):
    from src.dashboard.analytics import fixes_made
    h = _h(tmp_path)
    now = dt.datetime.now().timestamp()
    _seed(h, ts=now, raw="x", cleaned="X.", source="mobile")
    assert fixes_made(h.conn)["total"] == 0
    assert fixes_made(h.conn, include_mobile=True)["total"] == 1


# --- streak_heatmap ----------------------------------------------------------

def test_heatmap_emits_weeks_x_7_days(tmp_path):
    from src.dashboard.analytics import streak_heatmap
    h = _h(tmp_path)
    res = streak_heatmap(h.conn, weeks=4)
    assert len(res["days"]) == 28
    assert res["weeks"] == 4
    assert res["max"] == 0
    assert all(d["level"] == 0 for d in res["days"])


def test_heatmap_level_buckets(tmp_path):
    from src.dashboard.analytics import streak_heatmap
    h = _h(tmp_path)
    today = dt.date.today()
    for _ in range(11):
        _seed(h, ts=_ts(today), raw="x")
    res = streak_heatmap(h.conn, weeks=1)
    today_iso = today.isoformat()
    today_cell = next(d for d in res["days"] if d["date"] == today_iso)
    assert today_cell["count"] == 11
    assert today_cell["level"] == 4
    assert res["max"] == 11


def test_heatmap_oldest_first_ordering(tmp_path):
    from src.dashboard.analytics import streak_heatmap
    h = _h(tmp_path)
    res = streak_heatmap(h.conn, weeks=2)
    dates = [d["date"] for d in res["days"]]
    assert dates == sorted(dates)


# --- app_usage_breakdown -----------------------------------------------------

def test_app_usage_buckets_and_other(tmp_path):
    from src.dashboard.analytics import app_usage_breakdown
    h = _h(tmp_path)
    now = dt.datetime.now().timestamp()
    for _ in range(10):
        _seed(h, ts=now, raw="x", window_title="Visual Studio Code")
    for _ in range(3):
        _seed(h, ts=now, raw="x", window_title="Slack | Workspace")
    for _ in range(2):
        _seed(h, ts=now, raw="x", window_title="something obscure")
    res = app_usage_breakdown(h.conn, top_n=2)
    labels = [r["label"] for r in res]
    assert labels[0] == "Code"
    assert labels[1] == "Chat"
    # 2 obscure rows fall outside top_n=2 (Code, Chat) -> "Other"
    assert labels[-1] == "Other"
    assert sum(r["pct"] for r in res) == pytest.approx(1.0, rel=1e-6)


def test_app_usage_empty_when_no_data(tmp_path):
    from src.dashboard.analytics import app_usage_breakdown
    h = _h(tmp_path)
    assert app_usage_breakdown(h.conn) == []


def test_window_title_bucket_unknown_is_other():
    from src.dashboard.analytics import _bucket_window_title
    assert _bucket_window_title("") == "Other"
    assert _bucket_window_title(None) == "Other"
    assert _bucket_window_title("Cursor – README.md") == "Code"
    assert _bucket_window_title("Discord | Friends") == "Chat"
    assert _bucket_window_title("Gmail - johnson@example.com") == "Email"


# --- quality_trend -----------------------------------------------------------

def test_quality_trend_oldest_first(tmp_path):
    from src.dashboard.analytics import quality_trend
    h = _h(tmp_path)
    base = dt.datetime.now().timestamp()
    _seed(h, ts=base - 30, raw="a", quality=60.0)
    _seed(h, ts=base - 20, raw="a", quality=80.0)
    _seed(h, ts=base - 10, raw="a", quality=90.0)
    assert quality_trend(h.conn, limit=10) == [60.0, 80.0, 90.0]


def test_quality_trend_skips_null(tmp_path):
    from src.dashboard.analytics import quality_trend
    h = _h(tmp_path)
    now = dt.datetime.now().timestamp()
    _seed(h, ts=now, raw="a", quality=None)
    _seed(h, ts=now, raw="b", quality=70.0)
    assert quality_trend(h.conn) == [70.0]


# --- Insights route end-to-end ----------------------------------------------

def _client(history):
    from src.dashboard.app import make_app
    app_ref = types.SimpleNamespace(
        cfg={"dashboard": {"host": "127.0.0.1", "port": 8766}},
        history=history,
    )
    return make_app(app_ref).test_client()


def test_insights_route_renders_with_real_data(tmp_path):
    # PR-D reshape: /insights is now the Outcomes surface. Tiles cover
    # time saved, acceptance rate, latency p95; the heatmap is gone.
    h = _h(tmp_path)
    now = dt.datetime.now().timestamp()
    # Need ≥2 graded dictations to draw the trajectory sparkline.
    _seed(h, ts=now - 60, raw="hi", cleaned="Hi.", quality=78, window_title="Code")
    _seed(h, ts=now, raw="hello", cleaned="Hello.", quality=92, window_title="Code")
    r = _client(h).get("/insights", headers={"Host": "127.0.0.1:8766"})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Outcomes" in body
    # PR-D Wispr-style hero surfaces: WPM gauge, Fixes by Echo, Total words.
    assert "Words per minute" in body
    assert "Fixes made by Echo" in body
    assert "Total words dictated" in body
    assert "Desktop usage" in body
    # Quality trajectory polyline rendered when quality data exists.
    assert "polyline fill=\"none\"" in body
    # Heatmap was explicitly dropped.
    assert "hm-cell" not in body


def test_insights_route_empty_state(tmp_path):
    h = _h(tmp_path)
    r = _client(h).get("/insights", headers={"Host": "127.0.0.1:8766"})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Not enough usage to chart yet." in body
    # Sparkline section omitted when trend is empty.
    assert "polyline fill=\"none\"" not in body


def test_insights_route_handles_missing_history():
    from src.dashboard.app import make_app
    app_ref = types.SimpleNamespace(
        cfg={"dashboard": {"host": "127.0.0.1", "port": 8766}},
        history=None,
    )
    r = make_app(app_ref).test_client().get(
        "/insights", headers={"Host": "127.0.0.1:8766"}
    )
    assert r.status_code == 200
