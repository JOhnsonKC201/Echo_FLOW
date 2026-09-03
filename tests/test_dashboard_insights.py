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

def test_heatmap_is_monday_aligned_and_ends_today(tmp_path):
    from src.dashboard.analytics import streak_heatmap
    h = _h(tmp_path)
    res = streak_heatmap(h.conn, weeks=4)
    days = res["days"]
    # Window is snapped back to a Monday so the grid renders as clean Mon–Sun
    # columns; it always ends on today.
    assert days[0]["weekday"] == 0
    assert days[-1]["date"] == dt.date.today().isoformat()
    # Covers at least the requested span, and weeks == number of columns needed.
    assert len(days) >= 28
    assert res["weeks"] == (len(days) + 6) // 7
    # Each cell carries its column index (0-based week) for explicit placement.
    assert days[0]["week"] == 0
    assert days[-1]["week"] == res["weeks"] - 1
    assert res["max"] == 0
    assert all(d["level"] == 0 for d in days)


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
    """The Outcomes surface: a lead line, the stat rail, then four panels."""
    h = _h(tmp_path)
    now = dt.datetime.now().timestamp()
    # Two graded dictations is the minimum that draws the quality plot.
    _seed(h, ts=now - 60, raw="hi", cleaned="Hi.", quality=78, window_title="Code")
    _seed(h, ts=now, raw="hello", cleaned="Hello.", quality=92, window_title="Code")
    r = _client(h).get("/insights", headers={"Host": "127.0.0.1:8766"})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Outcomes" in body
    for heading in ("What Echo changed", "Where you dictate",
                    "When you dictate", "Cleanup quality"):
        assert heading in body
    for label in ("Typing time saved", "Speaking pace", "Words dictated",
                  "Kept as written", "Response time", "Current streak"):
        assert label in body
    assert 'polyline class="line"' in body


def test_every_tile_draws_a_chart(tmp_path):
    """The whole point of this surface: a headline with no series under it is
    a number you cannot sanity check. Each tile owns one chart element."""
    h = _h(tmp_path)
    now = dt.datetime.now().timestamp()
    for i in range(6):
        h.conn.execute(
            "INSERT INTO dictations(ts, window_title, style, language, duration_ms, "
            "raw_text, cleaned_text, source, latency_ms, quality_score) "
            "VALUES (?, 'Code', 'default', 'en', 5000, ?, ?, 'desktop', ?, 95)",
            (now - i * 3600, "a b c d e f g h i j", "A b c d e f g h i j.", 900 + i * 50),
        )
    h.conn.commit()
    body = _client(h).get(
        "/insights", headers={"Host": "127.0.0.1:8766"}).get_data(as_text=True)
    # One chart per tile: daily bars, pace scale, cumulative line, kept stacks,
    # latency histogram, day strip.
    assert body.count('class="oc-cols"') >= 2      # time saved + kept
    assert 'class="oc-cols tall"' in body          # latency histogram
    assert 'class="oc-scale"' in body              # pinned pace axis
    assert 'class="oc-line"' in body               # cumulative words
    assert 'class="oc-strip"' in body              # 14 day streak strip
    # And every chart is inside a recessed well, which is where charts live.
    assert body.count('class="oc-w"') >= 6


def test_insights_surfaces_the_metrics_the_route_computes(tmp_path):
    """time_saved, acceptance and latency were computed on every request and
    then dropped on the floor: the template never referenced them. Three
    round trips to SQLite per page load, rendering nothing."""
    h = _h(tmp_path)
    now = dt.datetime.now().timestamp()
    for i in range(4):
        h.conn.execute(
            "INSERT INTO dictations(ts, window_title, style, language, duration_ms, "
            "raw_text, cleaned_text, source, latency_ms) "
            "VALUES (?, 'Code', 'default', 'en', 4000, ?, ?, 'desktop', ?)",
            (now - i, "a b c d e f g h", "A b c d e f g h.", 1200 + i),
        )
    h.conn.commit()
    body = _client(h).get(
        "/insights", headers={"Host": "127.0.0.1:8766"}).get_data(as_text=True)
    assert "Typing time saved" in body
    assert "Kept as written" in body
    assert "Response time" in body
    assert "95th percentile" in body


def test_every_measurement_states_its_window(tmp_path):
    """Five windows share this page. Each has to say which one it is, or the
    numbers read as one comparable set. The windows come from the route, so a
    label cannot drift away from the query that produced it."""
    h = _h(tmp_path)
    now = dt.datetime.now().timestamp()
    _seed(h, ts=now, raw="hi there", cleaned="Hi there.", quality=90)
    _seed(h, ts=now - 30, raw="hello", cleaned="Hello.", quality=91)
    # Window labels are uppercased by CSS, not in the markup, so compare
    # case-insensitively: a purely visual casing change is not a regression.
    body = _client(h).get(
        "/insights", headers={"Host": "127.0.0.1:8766"}).get_data(as_text=True).lower()
    assert "last 30 days" in body       # time saved + app usage
    assert "last 7 days" in body        # acceptance
    assert "last 14 days" in body       # kept as written series
    assert "all time" in body           # words + what Echo changed
    assert "last 2 dictations" in body  # quality trend


def test_insights_offers_all_three_sources(tmp_path):
    """The toggle exposed Desktop and Mobile; the route has always accepted
    'all' as well."""
    body = _client(_h(tmp_path)).get(
        "/insights", headers={"Host": "127.0.0.1:8766"}).get_data(as_text=True)
    for src in ("desktop", "mobile", "all"):
        assert 'href="/insights?source=' + src + '"' in body


def test_insights_drops_the_meaningless_total(tmp_path):
    """fixes.total is words_corrected + dictionary_fixes: a word count added
    to a dictation count. It used to be the largest number on the page."""
    h = _h(tmp_path)
    now = dt.datetime.now().timestamp()
    _seed(h, ts=now, raw="um hello there friend", cleaned="Hello.")
    body = _client(h).get(
        "/insights", headers={"Host": "127.0.0.1:8766"}).get_data(as_text=True)
    assert "Fixes made by Echo" not in body
    # The two real components are still there, each labelled as what it counts.
    assert "Dictations Echo altered" in body
    assert "Characters changed" in body


def test_insights_route_empty_state(tmp_path):
    h = _h(tmp_path)
    r = _client(h).get("/insights", headers={"Host": "127.0.0.1:8766"})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Not enough usage to chart yet." in body
    # Sparkline section omitted when trend is empty.
    assert 'polyline class="line"' not in body
    # The lead has to say something true when there is nothing to report.
    assert "Nothing recorded yet." in body


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


# --- heatmap axis + activity ------------------------------------------------

def test_heatmap_carries_a_month_axis_and_active_days(tmp_path):
    from src.dashboard.analytics import streak_heatmap
    h = _h(tmp_path)
    today = dt.date.today()
    _seed(h, ts=_ts(today), raw="x")
    _seed(h, ts=_ts(today - dt.timedelta(days=1)), raw="x")
    res = streak_heatmap(h.conn, weeks=14)
    assert res["span"] == len(res["days"])
    assert res["active"] == 2
    # One label per month in the window, pinned to the column it starts on and
    # never past the end of the grid.
    assert res["months"], "a 14 week window always spans at least one month"
    weeks = [m["week"] for m in res["months"]]
    assert weeks == sorted(weeks)
    assert all(0 <= w < res["weeks"] for w in weeks)
    assert len({m["label"] for m in res["months"]}) == len(res["months"])


def test_heatmap_drops_months_too_narrow_to_label(tmp_path):
    from src.dashboard.analytics import streak_heatmap
    h = _h(tmp_path)
    res = streak_heatmap(h.conn, weeks=14)
    starts = [m["week"] for m in res["months"]] + [res["weeks"]]
    # Every kept label owns at least two columns, so labels cannot overlap.
    assert all(b - a >= 2 for a, b in zip(starts, starts[1:]))


# --- the source filter has to reach every headline metric --------------------

def _seed_rated(history, *, source, latency_ms, edited):
    now = dt.datetime.now().timestamp()
    history.conn.execute(
        "INSERT INTO dictations(ts, window_title, style, language, duration_ms, "
        "raw_text, cleaned_text, original_cleaned, source, latency_ms) "
        "VALUES (?, 'Code', 'default', 'en', 3000, 'a b', 'A b.', ?, ?, ?)",
        (now, "different." if edited else "A b.", source, latency_ms),
    )
    history.conn.commit()


def test_acceptance_rate_follows_the_source_filter(tmp_path):
    """It was pinned to source='desktop', so switching the page to Mobile
    moved every number except this one."""
    from src.dashboard.analytics import acceptance_rate
    h = _h(tmp_path)
    _seed_rated(h, source="desktop", latency_ms=100, edited=False)
    _seed_rated(h, source="mobile", latency_ms=900, edited=True)
    assert acceptance_rate(h.conn)["current"] == 1.0                    # desktop
    assert acceptance_rate(h.conn, include_mobile="mobile")["current"] == 0.0
    assert acceptance_rate(h.conn, include_mobile="all")["n_current"] == 2


def test_latency_percentiles_follow_the_source_filter(tmp_path):
    """It had no source filter at all, so mobile bridge rows leaked into the
    desktop reading."""
    from src.dashboard.analytics import latency_percentiles
    h = _h(tmp_path)
    _seed_rated(h, source="desktop", latency_ms=100, edited=False)
    _seed_rated(h, source="mobile", latency_ms=900, edited=False)
    assert latency_percentiles(h.conn)["p50"] == 100
    assert latency_percentiles(h.conn, include_mobile="mobile")["p50"] == 900
    assert latency_percentiles(h.conn, include_mobile="all")["n"] == 2


def test_apps_window_is_owned_by_the_caller(tmp_path):
    """The page prints "last N days" beside this list, so the caller sets N."""
    from src.dashboard.analytics import insights_payload
    h = _h(tmp_path)
    now = dt.datetime.now().timestamp()
    _seed(h, ts=now - (20 * 86400), raw="x", window_title="Visual Studio Code")
    assert insights_payload(h.conn, apps_window_days=30)["apps"]
    assert insights_payload(h.conn, apps_window_days=7)["apps"] == []


# --- the series behind the tiles --------------------------------------------

def test_daily_metrics_fills_quiet_days(tmp_path):
    """A series that omits empty days compresses time, so a fortnight off
    renders as a continuous run."""
    from src.dashboard.analytics import daily_metrics
    h = _h(tmp_path)
    today = dt.date.today()
    _seed(h, ts=_ts(today), raw="a b c", cleaned="A b c.")
    _seed(h, ts=_ts(today - dt.timedelta(days=4)), raw="d e", cleaned="D e.")
    rows = daily_metrics(h.conn, days=7)
    assert len(rows) == 7
    assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)
    assert rows[-1]["date"] == today.isoformat()
    assert rows[-1]["dictations"] == 1 and rows[-1]["words"] == 3
    assert rows[-2]["dictations"] == 0 and rows[-2]["words"] == 0
    assert sum(r["dictations"] for r in rows) == 2


def test_daily_saved_sums_to_the_headline(tmp_path):
    """The tile prints a total and draws the bars it came from. If the two are
    computed differently the chart quietly contradicts its own number."""
    import datetime as _dt
    from src.dashboard.analytics import daily_metrics, time_saved_ms
    h = _h(tmp_path)
    today = dt.date.today()
    for offset in (0, 1, 3, 9):
        _seed(h, ts=_ts(today - dt.timedelta(days=offset)),
              raw="one two three four five", cleaned="One two three four five.",
              duration_ms=2000)
    rows = daily_metrics(h.conn, days=30)
    start = dt.date.fromisoformat(rows[0]["date"])
    since = _dt.datetime.combine(start, _dt.time.min).timestamp()
    assert max(0, sum(r["saved_ms"] for r in rows)) == time_saved_ms(h.conn, since=since)


def test_daily_saved_is_not_clamped_per_day(tmp_path):
    """Clamping each day would make a slow day read as break-even and inflate
    the total above what time_saved_ms reports."""
    from src.dashboard.analytics import daily_metrics
    h = _h(tmp_path)
    # One word spoken over a full minute: far slower than typing it.
    _seed(h, ts=_ts(dt.date.today()), raw="hello", cleaned="hello",
          duration_ms=60_000)
    assert daily_metrics(h.conn, days=2)[-1]["saved_ms"] < 0


def test_kept_daily_reports_counts_not_a_rate(tmp_path):
    from src.dashboard.analytics import kept_daily
    h = _h(tmp_path)
    today = dt.date.today()
    h.conn.execute(
        "INSERT INTO dictations(ts, window_title, style, language, duration_ms, "
        "raw_text, cleaned_text, original_cleaned, source) "
        "VALUES (?, 'Code', 'default', 'en', 1000, 'a', 'A.', 'A.', 'desktop')",
        (_ts(today),))
    h.conn.execute(
        "INSERT INTO dictations(ts, window_title, style, language, duration_ms, "
        "raw_text, cleaned_text, original_cleaned, source) "
        "VALUES (?, 'Code', 'default', 'en', 1000, 'b', 'B edited.', 'B.', 'desktop')",
        (_ts(today),))
    h.conn.commit()
    rows = kept_daily(h.conn, days=3)
    assert len(rows) == 3
    assert rows[-1]["total"] == 2
    assert rows[-1]["kept"] == 1
    assert rows[-1]["edited"] == 1
    assert rows[0]["total"] == 0


def test_kept_daily_agrees_with_the_headline_rate(tmp_path):
    """Both read the same predicate, so a day of counts cannot disagree with
    the percentage printed above it."""
    from src.dashboard.analytics import acceptance_rate, kept_daily
    h = _h(tmp_path)
    now = dt.datetime.now().timestamp()
    for keep in (True, True, False):
        h.conn.execute(
            "INSERT INTO dictations(ts, window_title, style, language, duration_ms, "
            "raw_text, cleaned_text, original_cleaned, source) "
            "VALUES (?, 'Code', 'default', 'en', 1000, 'a', 'A.', ?, 'desktop')",
            (now, "A." if keep else "different."))
    h.conn.commit()
    rate = acceptance_rate(h.conn, days=1)
    rows = kept_daily(h.conn, days=1)
    assert rate["n_current"] == sum(r["total"] for r in rows)
    assert round(rate["current"] * 3) == sum(r["kept"] for r in rows)


def test_latency_histogram_axis_stops_at_p95(tmp_path):
    """A linear axis out to the true maximum lets one 40 second outlier squash
    every other sample into the first bin."""
    from src.dashboard.analytics import latency_histogram
    h = _h(tmp_path)
    now = dt.datetime.now().timestamp()
    values = [1000] * 19 + [40_000]
    for i, ms in enumerate(values):
        h.conn.execute(
            "INSERT INTO dictations(ts, window_title, style, language, duration_ms, "
            "raw_text, cleaned_text, source, latency_ms) "
            "VALUES (?, 'Code', 'default', 'en', 1000, 'a', 'A.', 'desktop', ?)",
            (now - i, ms))
    h.conn.commit()
    hist = latency_histogram(h.conn, bins=10)
    assert hist["n"] == 20
    assert hist["axis_max"] == hist["p95"]
    assert hist["axis_max"] < 40_000          # the outlier does not set the axis
    assert hist["over"] >= 1                  # and is accounted for, not dropped
    assert sum(b["count"] for b in hist["bins"]) == 20
    assert 0.0 <= hist["p50_pos"] <= 1.0 and hist["p95_pos"] == 1.0


def test_latency_histogram_is_empty_without_data(tmp_path):
    from src.dashboard.analytics import latency_histogram
    assert latency_histogram(_h(tmp_path).conn)["bins"] == []


def test_wpm_profile_axis_is_pinned_and_matches_the_headline(tmp_path):
    """The mark can only move because you did. And the tile's number is the
    same object the distribution was built from."""
    from src.dashboard.analytics import current_wpm, wpm_profile
    h = _h(tmp_path)
    now = dt.datetime.now().timestamp()
    _seed(h, ts=now, raw="one two three four five six", duration_ms=3000)
    _seed(h, ts=now, raw="one two three", duration_ms=3000)
    quiet = wpm_profile(h.conn)
    assert quiet["scale_max"] == 240
    assert quiet["mean"] == current_wpm(h.conn)
    assert 0 <= quiet["mean_pos"] <= 1
    assert quiet["baseline_pos"] == quiet["baseline"] / 240
    # Buckets carry their own geometry so they can be laid out on that axis.
    assert quiet["buckets"][0]["lo"] == 0
    assert quiet["buckets"][-1]["hi"] == 240
    assert sum(b["count"] for b in quiet["buckets"]) == quiet["n"]
