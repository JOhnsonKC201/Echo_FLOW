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


# --- Triage: which cards want a human look ---------------------------------

def _row(text, *, q=98.0, rating=None, original=None):
    return {"cleaned_text": text, "quality_score": q,
            "user_rating": rating, "original_cleaned": original}


def test_review_reasons_clean_sentence_is_quiet():
    """The 94% case: a normal, well-scored dictation asks nothing of the user."""
    assert inbox.review_reasons(
        _row("I think we can have a better visual on this slide. What do you think?")) == []


def test_review_reasons_flags_the_real_world_false_positives():
    """Regression against the two rows that motivated this feature.

    Both are grammatically complete sentences and both scored 96+, which is why
    "auto-approve if it is a complete sentence" would have approved exactly the
    dictations the user rejected. Length and the missing terminal stop are what
    actually catch them.
    """
    assert "very short" in inbox.review_reasons(_row("Again.", q=99.0))
    assert "very short" in inbox.review_reasons(_row("LinkedIn is.", q=97.0))


def test_review_reasons_flags_truncation_and_low_quality():
    assert "looks cut off" in inbox.review_reasons(
        _row("I was about to say that the whole thing"))
    assert "low quality score" in inbox.review_reasons(
        _row("A perfectly ordinary sentence that simply scored badly.", q=41.0))
    # At/above the UI's own "good" line, quality alone must not flag.
    assert "low quality score" not in inbox.review_reasons(
        _row("A perfectly ordinary sentence that scored fine.", q=75.0))


def test_needs_review_keeps_acted_on_rows_visible():
    """Acting on a card must not make it vanish into the collapsed pile.

    These stay visible via needs_review, NOT via review_reasons: the card is
    already wearing a "marked bad" pill, so repeating it as a reason would print
    the same fact twice side by side.
    """
    long_ok = "This is a long and perfectly ordinary dictation that ends properly."
    assert inbox.needs_review(_row(long_ok)) is False
    assert inbox.needs_review(_row(long_ok, rating=-1)) is True
    assert inbox.review_reasons(_row(long_ok, rating=-1)) == []      # pill says it
    assert inbox.needs_review(
        _row(long_ok, original="Something the model originally produced.")) is True
    # Approving is not a reason to keep nagging.
    assert inbox.needs_review(_row(long_ok, rating=1)) is False


def test_needs_review_follows_detection_reasons():
    assert inbox.needs_review(_row("Again.", q=99.0)) is True
    assert inbox.needs_review(
        _row("A long and complete sentence that ends properly.")) is False


def test_review_reasons_pins_both_thresholds_exactly():
    """Both boundaries, from either side.

    Without these a mutation that quietly moves the line by one (q<74 instead of
    q<75, or <3 words instead of <=3) changes which rows get surfaced and every
    other test still passes.
    """
    # quality: 74 flags, 75 does not
    assert "low quality score" in inbox.review_reasons(
        _row("A perfectly ordinary sentence that ends properly.", q=74.0))
    assert "low quality score" not in inbox.review_reasons(
        _row("A perfectly ordinary sentence that ends properly.", q=75.0))
    # length: 3 words flags, 4 does not
    assert "very short" in inbox.review_reasons(_row("One two three."))
    assert "very short" not in inbox.review_reasons(_row("One two three four."))


def test_review_reasons_handles_languages_that_are_not_english():
    """whisper.language offers 16 languages plus auto-detect.

    An ASCII-only terminator set flagged every correctly punctuated Chinese,
    Japanese, Hindi and Arabic sentence as "looks cut off", and because CJK has
    no inter-word spaces, str.split() made every one of them "very short" too -
    so for those users the quiet pile would have been permanently empty.
    """
    zh = "这是一个完整的句子。"
    ja = "これは完全な文です。"
    hi = "यह एक पूरा वाक्य है।"
    ar = "ما هو رأيك في هذا؟"
    for label, text in (("zh", zh), ("ja", ja), ("hi", hi), ("ar", ar)):
        assert inbox.review_reasons(_row(text)) == [], f"{label} was flagged: {text!r}"
    # A genuinely clipped CJK utterance is still caught.
    assert "very short" in inbox.review_reasons(_row("好。"))
    # Closing quotes and guillemets count as terminated.
    assert inbox.review_reasons(_row("He said “ship it”")) == []
    assert inbox.review_reasons(_row("Il a dit «bonjour»")) == []
    # A trailing comma still reads as cut off, unlike cleanup.py's own rule.
    assert "looks cut off" in inbox.review_reasons(_row("I went to the store and then,"))


def test_whitespace_only_result_is_surfaced_not_hidden():
    """A blank result is a broken row. The edit route writes form input through
    with no validation, so this is reachable, and it must not sort itself into
    the quiet pile."""
    assert inbox.review_reasons(_row("   ")) == ["empty result"]
    assert inbox.needs_review(_row("   ")) is True
    # A genuinely empty cell is absence, not breakage.
    assert inbox.review_reasons(_row("")) == []
    assert inbox.needs_review(_row("")) is False


def test_needs_review_strips_both_sides_before_comparing():
    """history.log writes one string into both columns, so an unedited row is
    identical. Stripping only cleaned_text made outer whitespace look like a
    permanent edit - reachable via cleanup.provider "none", which returns the
    transcript verbatim."""
    same = " Send the report today. "
    assert inbox.needs_review(_row(same, original=same)) is False
    assert inbox.needs_review(
        _row(" Send the report today. ", original="Send something else.")) is True


def test_review_reasons_ignores_empty_and_missing_score():
    assert inbox.review_reasons(_row("")) == []
    # whitespace-only is NOT "nothing to judge" - see
    # test_whitespace_only_result_is_surfaced_not_hidden.
    # quality_score is nullable; absence must not read as zero.
    assert "low quality score" not in inbox.review_reasons(
        _row("A sentence with no score recorded at all.", q=None))


def test_inbox_rows_attaches_reasons(tmp_path):
    h = History(str(tmp_path / "h.db"))
    h.log(window_title="t", style="default", language="en", duration_ms=100,
          raw_text="again", cleaned_text="Again.", quality_score=99.0)
    h.log(window_title="t", style="default", language="en", duration_ms=100,
          raw_text="a long one", cleaned_text="A long and complete sentence here.",
          quality_score=98.0)
    rows = inbox.inbox_rows(h.conn, n=10)
    by_text = {r["cleaned_text"]: r for r in rows}
    assert "very short" in by_text["Again."]["reasons"]
    assert by_text["Again."]["needs_review"] is True
    assert by_text["A long and complete sentence here."]["reasons"] == []
    assert by_text["A long and complete sentence here."]["needs_review"] is False


def _seed_one_of_each(app_ref):
    """A flagged row and a quiet row. Returns (flagged_id, quiet_id)."""
    flagged = app_ref.history.log(
        window_title="t", style="default", language="en", duration_ms=100,
        raw_text="again", cleaned_text="Again.", quality_score=99.0)
    quiet = app_ref.history.log(
        window_title="t", style="default", language="en", duration_ms=100,
        raw_text="fine", cleaned_text="A long and complete sentence that is fine.",
        quality_score=98.0)
    return flagged, quiet


def test_home_puts_each_card_in_the_right_group(tmp_path):
    """Assert WHICH pile each card lands in, by document position.

    The obvious version of this test - substring checks for both headings, both
    texts, and a count of /inbox/rate forms - passes even when the two groups are
    swapped, because every card renders through the same macro no matter which
    pile holds it. Position relative to the two group markers is what actually
    pins the behaviour.
    """
    client, app_ref = _client(tmp_path)
    flagged_id, quiet_id = _seed_one_of_each(app_ref)

    body = client.get("/", headers=HOST).data.decode("utf-8")
    needs_at = body.index("Needs a look")
    fine_at = body.index("Looks fine")
    flagged_at = body.index(f'id="d-{flagged_id}"')
    quiet_at = body.index(f'id="d-{quiet_id}"')

    assert needs_at < flagged_at < fine_at, "the flagged card must sit in the visible pile"
    assert fine_at < quiet_at, "the clean card must sit inside the collapsed pile"
    # The flagged card explains itself rather than showing an opaque badge.
    assert "very short" in body
    # Collapsing must not strip the actions off the quiet card.
    assert body.count("/inbox/rate") >= 4


def test_quiet_group_opens_only_when_nothing_is_flagged(tmp_path):
    """A page whose only content is a collapsed disclosure would look empty."""
    client, app_ref = _client(tmp_path)
    app_ref.history.log(window_title="t", style="default", language="en",
                        duration_ms=100, raw_text="fine",
                        cleaned_text="A long and complete sentence that is fine.",
                        quality_score=98.0)
    body = client.get("/", headers=HOST).data.decode("utf-8")
    assert "Needs a look" not in body          # nothing flagged, so no heading
    assert '<details class="inbox-quiet" open>' in body

    # Add a flagged row and the pile collapses again.
    app_ref.history.log(window_title="t", style="default", language="en",
                        duration_ms=100, raw_text="again", cleaned_text="Again.",
                        quality_score=99.0)
    body = client.get("/", headers=HOST).data.decode("utf-8")
    assert "Needs a look" in body
    assert '<details class="inbox-quiet">' in body


def test_all_flagged_hides_the_quiet_group(tmp_path):
    client, app_ref = _client(tmp_path)
    app_ref.history.log(window_title="t", style="default", language="en",
                        duration_ms=100, raw_text="again", cleaned_text="Again.",
                        quality_score=99.0)
    body = client.get("/", headers=HOST).data.decode("utf-8")
    assert "Needs a look" in body
    assert "Looks fine" not in body            # no empty "(0)" section


def test_editing_a_row_brings_it_back_into_view(tmp_path):
    """The edit route writes cleaned_text and leaves original_cleaned stale, which
    is precisely what marks a row as edited. Exercised end to end, because a
    future 'tidy-up' that also updated original_cleaned would silently disable it
    and every unit test would still pass."""
    client, app_ref = _client(tmp_path)
    did = app_ref.history.log(
        window_title="t", style="default", language="en", duration_ms=100,
        raw_text="fine", cleaned_text="A long and complete sentence that is fine.",
        quality_score=98.0)
    assert inbox.inbox_rows(app_ref.history.conn)[0]["needs_review"] is False

    client.post(f"/inbox/{did}/edit", headers=HOST,
                data={"cleaned_text": "A long and complete sentence, now corrected."})
    assert inbox.inbox_rows(app_ref.history.conn)[0]["needs_review"] is True


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
