"""PR-C — Home → Inbox: diff rendering, rating writes, edit round-trip."""
from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from src.history import History
from src.dashboard import inbox


REPO_CFG = Path(__file__).resolve().parent.parent / "config.yaml"


class _App:
    def __init__(self, cfg, cfg_path, history):
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.history = history


HOST = {"Host": "127.0.0.1:8766"}


def _client(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    shutil.copy(REPO_CFG, cfg_path)
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg["dashboard"]["onboarded"] = True
    h = History(str(tmp_path / "h.db"))
    app_ref = _App(cfg, cfg_path, h)
    from src.dashboard.app import make_app
    return make_app(app_ref).test_client(), app_ref


# --- Pure helpers ----------------------------------------------------------

def test_render_diff_empty_returns_empty():
    assert inbox.render_diff("", "") == []
    assert inbox.render_diff("same", "same")  # all equal still emits line markers


def test_render_diff_marks_changes():
    out = inbox.render_diff("hello wrold", "Hello world.")
    kinds = [k for k, _ in out]
    # At least one delete + one add somewhere in the stream.
    assert "del-line-start" in kinds
    assert "add-line-start" in kinds
    assert any(k == "del" for k in kinds) or any(k == "add" for k in kinds)


def test_has_diff_detects_changes():
    assert inbox.has_diff("a", "b") is True
    assert inbox.has_diff(" hi ", "hi") is False
    assert inbox.has_diff("", "") is False


def test_format_ts_handles_zero_and_garbage():
    assert inbox.format_ts(0) == ""
    assert inbox.format_ts(None) == ""
    # ts=1700000000 is "Nov 14 ..." — format includes a month abbreviation.
    rendered = inbox.format_ts(1700000000)
    assert any(m in rendered for m in
               ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"))


def test_inbox_rows_newest_first(tmp_path):
    h = History(str(tmp_path / "h.db"))
    a = h.log(window_title="t", style="default", language="en", duration_ms=100,
              raw_text="first", cleaned_text="First.")
    b = h.log(window_title="t", style="default", language="en", duration_ms=100,
              raw_text="second", cleaned_text="Second.")
    rows = inbox.inbox_rows(h.conn)
    assert [r["id"] for r in rows] == [b, a]
    assert rows[0]["cleaned_text"] == "Second."


def test_inbox_rows_respects_n(tmp_path):
    h = History(str(tmp_path / "h.db"))
    for i in range(5):
        h.log(window_title="t", style="default", language="en", duration_ms=10,
              raw_text=f"r{i}", cleaned_text=f"c{i}")
    rows = inbox.inbox_rows(h.conn, n=2)
    assert len(rows) == 2


# --- Route round-trips -----------------------------------------------------

def test_home_renders_inbox_when_dictations_exist(tmp_path):
    client, app_ref = _client(tmp_path)
    app_ref.history.log(window_title="t", style="default", language="en",
                        duration_ms=200, raw_text="hello wrold",
                        cleaned_text="Hello world.", latency_ms=300)
    r = client.get("/", headers=HOST)
    assert r.status_code == 200
    assert b"Inbox" in r.data
    assert b"Hello world." in r.data
    assert b"Approve" in r.data
    assert b"Mark bad" in r.data


def test_home_renders_empty_state(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/", headers=HOST)
    assert r.status_code == 200
    assert b"No dictations yet" in r.data


def test_home_shows_today_summary_card(tmp_path):
    client, app_ref = _client(tmp_path)
    app_ref.history.log(window_title="t", style="default", language="en",
                        duration_ms=200, raw_text="x", cleaned_text="x",
                        latency_ms=420)
    r = client.get("/", headers=HOST)
    assert b"today" in r.data
    assert b"acceptance" in r.data


def test_inbox_rate_approve(tmp_path):
    client, app_ref = _client(tmp_path)
    did = app_ref.history.log(window_title="t", style="default", language="en",
                              duration_ms=10, raw_text="r", cleaned_text="c")
    r = client.post("/inbox/rate", headers=HOST,
                    data={"id": str(did), "rating": "1"})
    assert r.status_code == 302
    row = app_ref.history.conn.execute(
        "SELECT user_rating FROM dictations WHERE id = ?", (did,)
    ).fetchone()
    assert row[0] == 1


def test_inbox_rate_clear(tmp_path):
    client, app_ref = _client(tmp_path)
    did = app_ref.history.log(window_title="t", style="default", language="en",
                              duration_ms=10, raw_text="r", cleaned_text="c")
    client.post("/inbox/rate", headers=HOST, data={"id": str(did), "rating": "1"})
    client.post("/inbox/rate", headers=HOST, data={"id": str(did), "rating": ""})
    row = app_ref.history.conn.execute(
        "SELECT user_rating FROM dictations WHERE id = ?", (did,)
    ).fetchone()
    assert row[0] is None


def test_inbox_rate_rejects_garbage_rating(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/inbox/rate", headers=HOST,
                    data={"id": "1", "rating": "7"})
    assert r.status_code == 302
    assert "Rating%20must" in r.headers["Location"]


def test_inbox_edit_get_404_for_missing(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/inbox/9999/edit", headers=HOST)
    assert r.status_code == 404


def test_inbox_edit_round_trip(tmp_path):
    client, app_ref = _client(tmp_path)
    did = app_ref.history.log(window_title="t", style="default", language="en",
                              duration_ms=10, raw_text="r", cleaned_text="old")
    r = client.post(f"/inbox/{did}/edit", headers=HOST,
                    data={"cleaned_text": "manually edited"})
    assert r.status_code == 302
    row = app_ref.history.conn.execute(
        "SELECT cleaned_text FROM dictations WHERE id = ?", (did,)
    ).fetchone()
    assert row[0] == "manually edited"


# --- Analytics outcomes ---------------------------------------------------

def test_humanize_ms_thresholds():
    from src.dashboard import analytics
    assert analytics.humanize_ms(0) == "0 ms"
    assert analytics.humanize_ms(500) == "500 ms"
    assert analytics.humanize_ms(1500) == "1.5 s"
    assert analytics.humanize_ms(120_000) == "2 m"
    assert analytics.humanize_ms(2 * 3_600_000) == "2h"
    assert analytics.humanize_ms(3_660_000) == "1h 1m"


def test_latency_percentiles_empty(tmp_path):
    from src.dashboard import analytics
    h = History(str(tmp_path / "h.db"))
    out = analytics.latency_percentiles(h.conn)
    assert out == {"p50": None, "p95": None, "n": 0}


def test_latency_percentiles_with_data(tmp_path):
    from src.dashboard import analytics
    h = History(str(tmp_path / "h.db"))
    for v in (100, 200, 300, 400, 500):
        h.log(window_title="t", style="default", language="en", duration_ms=10,
              raw_text="r", cleaned_text="c", latency_ms=v)
    out = analytics.latency_percentiles(h.conn)
    assert out["n"] == 5
    # 5 samples: indices for p50 → 2 (200), p95 → 4 (500)
    assert out["p50"] == 300
    assert out["p95"] == 500


def test_acceptance_rate_no_data(tmp_path):
    from src.dashboard import analytics
    h = History(str(tmp_path / "h.db"))
    out = analytics.acceptance_rate(h.conn)
    assert out["current"] == 0.0
    assert out["n_current"] == 0


def test_acceptance_rate_bad_rating_counted_unaccepted(tmp_path):
    from src.dashboard import analytics
    h = History(str(tmp_path / "h.db"))
    a = h.log(window_title="t", style="default", language="en", duration_ms=10,
              raw_text="r", cleaned_text="c")  # accepted by default
    b = h.log(window_title="t", style="default", language="en", duration_ms=10,
              raw_text="r", cleaned_text="c")
    h.rate_dictation(b, -1)
    out = analytics.acceptance_rate(h.conn)
    assert out["n_current"] == 2
    assert out["current"] == 0.5  # one accepted, one bad


def test_time_saved_ms_positive_on_real_words(tmp_path):
    from src.dashboard import analytics
    h = History(str(tmp_path / "h.db"))
    # 20-word dictation taking 5s → typing at 40 wpm = 30s baseline,
    # so we should report ~25s saved.
    text = " ".join(["word"] * 20)
    h.log(window_title="t", style="default", language="en", duration_ms=5000,
          raw_text=text, cleaned_text=text)
    saved = analytics.time_saved_ms(h.conn)
    assert saved > 20_000 and saved < 35_000


def test_today_summary_contract(tmp_path):
    from src.dashboard import analytics
    h = History(str(tmp_path / "h.db"))
    out = analytics.today_summary(h.conn)
    assert set(out.keys()) >= {"count", "time_saved_ms", "acceptance", "latency"}
