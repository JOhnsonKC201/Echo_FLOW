"""Unit tests for src/voice_profile.py: normalizing the humanize setting and
assembling the "voice profile" block from writing samples and mined dictations.

The profile is built from a real History database in tmp_path. The clock and
the sample reader are faked only where a test needs to control time or force a
failure. The module caches its result, so every test starts and ends with the
cache cleared.
"""
from __future__ import annotations

import itertools
import sqlite3
import threading
import types

import pytest

from src import voice_profile as vp
from src.dashboard import voice_samples as vs

pytestmark = pytest.mark.unit

SAMPLES_HEADER = "WRITING SAMPLES (how you actually write):"
EXEMPLARS_HEADER = "DICTATIONS YOU KEPT OR CORRECTED (your phrasing, your words):"
THREADS = 12
JOIN_TIMEOUT_SECONDS = 20

_clock = itertools.count(1_000)


@pytest.fixture(autouse=True)
def _fresh_cache():
    vp.invalidate()
    yield
    vp.invalidate()


@pytest.fixture
def hist(temp_db):
    return temp_db[0]


@pytest.fixture
def clock(monkeypatch):
    """A settable clock in place of time.time() inside the module."""
    now = [1_000.0]
    monkeypatch.setattr(vp, "time", types.SimpleNamespace(time=lambda: now[0]))
    return now


def add_dictation(h, cleaned, *, original=None, rating=None, source="desktop", ts=None):
    """One history row. `ts` defaults to a strictly increasing value so that
    insertion order is also newest-last order."""
    h.conn.execute(
        "INSERT INTO dictations(ts, style, raw_text, cleaned_text, "
        "original_cleaned, user_rating, source) VALUES (?,?,?,?,?,?,?)",
        (next(_clock) if ts is None else ts, "polished", "raw", cleaned,
         original, rating, source),
    )
    h.conn.commit()


def approved(h, text, **kw):
    add_dictation(h, text, rating=1, **kw)


def bullets(profile: str) -> list[str]:
    return [ln[2:] for ln in profile.splitlines() if ln.startswith("- ")]


def sample_blocks(profile: str) -> list[str]:
    """Writing samples of a profile that has no exemplar section."""
    body = profile.split(SAMPLES_HEADER + "\n", 1)[1]
    return body.split("\n---\n")


# --- humanize_mode_for_cfg --------------------------------------------------

@pytest.mark.parametrize("cfg,expected", [
    (None, "off"),
    ({}, "off"),
    ({"other": True}, "off"),
    ({"humanize": None}, "off"),
    ({"humanize": True}, "on"),
    ({"humanize": False}, "off"),
    ({"humanize": 1}, "on"),
    ({"humanize": 0}, "off"),
    ({"humanize": ["x"]}, "on"),
    ({"humanize": []}, "off"),
])
def test_humanize_mode_from_non_string_values(cfg, expected):
    assert vp.humanize_mode_for_cfg(cfg) == expected


@pytest.mark.parametrize("value,expected", [
    ("shadow", "shadow"), ("SHADOW", "shadow"), ("  Shadow  ", "shadow"),
    ("on", "on"), ("ON", "on"), ("true", "on"), ("True", "on"),
    ("1", "on"), ("yes", "on"), ("YES", "on"), (" on ", "on"),
    ("off", "off"), ("false", "off"), ("0", "off"), ("no", "off"),
    ("", "off"), ("   ", "off"), ("maybe", "off"), ("enabled", "off"),
])
def test_humanize_mode_from_string_values(value, expected):
    assert vp.humanize_mode_for_cfg({"humanize": value}) == expected


def test_humanize_applies_to_prose_styles_not_code_or_prompt():
    assert isinstance(vp.HUMANIZE_STYLES, frozenset)
    assert {"polished", "medium", "default", "casual", "email"} <= vp.HUMANIZE_STYLES
    assert not {"code", "prompt"} & vp.HUMANIZE_STYLES


# --- build(): shape ---------------------------------------------------------

def test_no_history_means_no_profile():
    assert vp.build(None) == ""


def test_empty_database_means_no_profile(hist):
    assert vp.build(hist) == ""


def test_samples_and_exemplars_form_two_blocks_separated_by_a_blank_line(hist):
    vs.add_sample(hist.conn, "I write short sentences.")
    approved(hist, "Ship it on Friday afternoon.")
    assert vp.build(hist) == (
        f"{SAMPLES_HEADER}\nI write short sentences.\n\n"
        f"{EXEMPLARS_HEADER}\n- Ship it on Friday afternoon."
    )


def test_only_samples_gives_only_the_samples_block(hist):
    vs.add_sample(hist.conn, "Just my own writing.")
    profile = vp.build(hist)
    assert profile == f"{SAMPLES_HEADER}\nJust my own writing."
    assert EXEMPLARS_HEADER not in profile


def test_only_exemplars_gives_only_the_exemplars_block(hist):
    approved(hist, "Thumbs up on this one.")
    profile = vp.build(hist)
    assert profile == f"{EXEMPLARS_HEADER}\n- Thumbs up on this one."
    assert SAMPLES_HEADER not in profile


@pytest.mark.parametrize("style", ["polished", "code", "prompt", "no-such-style"])
def test_profile_is_the_same_whatever_style_or_retriever_is_passed(hist, style):
    vs.add_sample(hist.conn, "Voice is not per style.")
    vp.invalidate()
    baseline = vp.build(hist)
    vp.invalidate()
    assert vp.build(hist, object(), style) == baseline


# --- build(): writing samples ----------------------------------------------

def test_samples_are_newest_first_and_separated_by_rules(hist):
    vs.add_sample(hist.conn, "oldest sample")
    vs.add_sample(hist.conn, "middle sample")
    vs.add_sample(hist.conn, "newest sample")
    assert sample_blocks(vp.build(hist)) == [
        "newest sample", "middle sample", "oldest sample"]


def test_disabled_samples_are_left_out(hist):
    vs.add_sample(hist.conn, "kept sample")
    off = vs.add_sample(hist.conn, "switched off sample")
    hist.conn.execute("UPDATE voice_samples SET enabled = 0 WHERE id = ?", (off,))
    hist.conn.commit()
    assert sample_blocks(vp.build(hist)) == ["kept sample"]


def test_samples_that_exactly_fill_the_budget_all_fit(hist):
    half = vp.SAMPLE_CHAR_BUDGET // 2
    for ch in "123":
        vs.add_sample(hist.conn, ch * half)
    # newest two fill the budget exactly; the oldest no longer fits
    assert sample_blocks(vp.build(hist)) == ["3" * half, "2" * half]


def test_a_sample_one_character_over_the_budget_is_dropped(hist):
    half = vp.SAMPLE_CHAR_BUDGET // 2
    vs.add_sample(hist.conn, "1" * half)
    vs.add_sample(hist.conn, "2" * (half + 1))
    assert sample_blocks(vp.build(hist)) == ["2" * (half + 1)]


def test_a_single_oversized_sample_is_cut_to_the_budget(hist):
    vs.add_sample(hist.conn, "a" * (vp.SAMPLE_CHAR_BUDGET + 1_000))
    assert sample_blocks(vp.build(hist)) == ["a" * vp.SAMPLE_CHAR_BUDGET]


def test_a_sample_exactly_the_budget_is_kept_whole(hist):
    vs.add_sample(hist.conn, "older")
    vs.add_sample(hist.conn, "b" * vp.SAMPLE_CHAR_BUDGET)
    assert sample_blocks(vp.build(hist)) == ["b" * vp.SAMPLE_CHAR_BUDGET]


def test_blank_and_missing_samples_are_skipped(hist, monkeypatch):
    monkeypatch.setattr(vp._vs, "enabled_texts",
                        lambda conn: [None, "", "   \n", "  padded sample  "])
    assert sample_blocks(vp.build(hist)) == ["padded sample"]


def test_a_failing_sample_reader_costs_only_the_samples(hist, monkeypatch):
    approved(hist, "Exemplar survives the failure.")

    def broken(conn):
        raise sqlite3.OperationalError("no such table: voice_samples")

    monkeypatch.setattr(vp._vs, "enabled_texts", broken)
    assert vp.build(hist) == f"{EXEMPLARS_HEADER}\n- Exemplar survives the failure."


def test_unicode_samples_round_trip(hist):
    text = "caf" + chr(0xE9) + " " + chr(0x1F600) + " 'quoted'; DROP TABLE x;--"
    vs.add_sample(hist.conn, text)
    assert sample_blocks(vp.build(hist)) == [text]


# --- build(): mined exemplars ----------------------------------------------

def test_approved_and_corrected_lines_are_kept_everything_else_is_not(hist):
    approved(hist, "Approved by the user.")
    add_dictation(hist, "Corrected by the user.", original="what the model wrote")
    add_dictation(hist, "Passively accepted as is.", original="Passively accepted as is.")
    add_dictation(hist, "Never reviewed at all.")
    add_dictation(hist, "Marked bad by the user.", rating=-1)
    assert sorted(bullets(vp.build(hist))) == [
        "Approved by the user.", "Corrected by the user."]


def test_exemplars_are_newest_first(hist):
    for n in range(3):
        approved(hist, f"Exemplar number {n} here.")
    assert bullets(vp.build(hist)) == [
        "Exemplar number 2 here.", "Exemplar number 1 here.", "Exemplar number 0 here."]


def test_exemplar_length_floor_is_inclusive(hist):
    approved(hist, "x" * (vp.MIN_EXEMPLAR_CHARS - 1))
    approved(hist, "y" * vp.MIN_EXEMPLAR_CHARS)
    assert bullets(vp.build(hist)) == ["y" * vp.MIN_EXEMPLAR_CHARS]


def test_mobile_submissions_are_never_a_voice_signal(hist):
    approved(hist, "Approved on the phone.", source="mobile")
    add_dictation(hist, "Corrected on the phone.", original="x", source="mobile")
    approved(hist, "Approved at the desk.", source="desktop")
    assert bullets(vp.build(hist)) == ["Approved at the desk."]


def test_rows_with_no_cleaned_text_are_ignored(hist):
    add_dictation(hist, None, rating=1)
    assert vp.build(hist) == ""


def test_at_most_max_exemplars_are_kept_and_they_are_the_newest(hist):
    total = vp.MAX_EXEMPLARS + 5
    for n in range(total):
        approved(hist, f"Distinct exemplar line {n:02d}.")
    got = bullets(vp.build(hist))
    assert got == [f"Distinct exemplar line {n:02d}."
                   for n in range(total - 1, total - 1 - vp.MAX_EXEMPLARS, -1)]


def test_duplicates_ignore_case_and_padding_and_the_newest_spelling_wins(hist):
    approved(hist, "see you at the standup")
    approved(hist, "  See You At The Standup  ")
    assert bullets(vp.build(hist)) == ["See You At The Standup"]


def test_whitespace_only_rows_are_skipped_and_do_not_use_up_a_slot(hist):
    for n in range(vp.MAX_EXEMPLARS):
        approved(hist, f"Real exemplar line {n}.")
    approved(hist, " " * (vp.MIN_EXEMPLAR_CHARS + 3))     # newest, but blank
    assert len(bullets(vp.build(hist))) == vp.MAX_EXEMPLARS


def test_exemplar_text_is_stored_verbatim_and_sql_text_is_inert(hist):
    text = "x'); DROP TABLE dictations;-- " + chr(0x1F600)
    approved(hist, text)
    assert bullets(vp.build(hist)) == [text.strip()]
    assert hist.conn.execute("SELECT COUNT(*) FROM dictations").fetchone()[0] == 1


def test_a_large_history_is_reduced_to_the_cap(hist):
    rows = [(float(i), "polished", "raw", f"Bulk exemplar number {i:05d}.", None, 1, "desktop")
            for i in range(10_000)]
    hist.conn.executemany(
        "INSERT INTO dictations(ts, style, raw_text, cleaned_text, "
        "original_cleaned, user_rating, source) VALUES (?,?,?,?,?,?,?)", rows)
    hist.conn.commit()
    got = bullets(vp.build(hist))
    assert len(got) == vp.MAX_EXEMPLARS
    assert got[0] == "Bulk exemplar number 09999."


# --- build(): unusable databases -------------------------------------------

def test_a_closed_connection_gives_an_empty_profile_not_an_error(hist):
    hist.conn.close()
    assert vp.build(hist) == ""


def test_a_database_without_the_expected_tables_gives_an_empty_profile():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE dictations (id INTEGER PRIMARY KEY, ts REAL, cleaned_text TEXT)")
    try:
        assert vp.build(types.SimpleNamespace(conn=conn)) == ""
    finally:
        conn.close()


# --- build(): the cache -----------------------------------------------------

def test_profile_is_reused_until_the_ttl_runs_out(hist, clock):
    start = clock[0]
    vs.add_sample(hist.conn, "first sample")
    first = vp.build(hist)
    vs.add_sample(hist.conn, "second sample")
    clock[0] = start + vp._CACHE_TTL - 0.5
    assert vp.build(hist) == first
    clock[0] = start + vp._CACHE_TTL            # exactly the TTL: expired
    assert "second sample" in vp.build(hist)


def test_a_rebuild_restarts_the_ttl(hist, clock):
    start = clock[0]
    vs.add_sample(hist.conn, "first sample")
    vp.build(hist)
    clock[0] = start + vp._CACHE_TTL
    vp.build(hist)                              # rebuilt at t = TTL
    vs.add_sample(hist.conn, "second sample")
    clock[0] = start + 2 * vp._CACHE_TTL - 1
    assert "second sample" not in vp.build(hist)


def test_an_empty_profile_is_cached_too_until_invalidated(hist):
    assert vp.build(hist) == ""
    vs.add_sample(hist.conn, "my first sample")
    assert vp.build(hist) == ""
    vp.invalidate()
    assert "my first sample" in vp.build(hist)


def test_invalidate_with_nothing_cached_is_harmless():
    vp.invalidate()
    vp.invalidate()
    assert vp.build(None) == ""


# --- build(): concurrency ---------------------------------------------------

def test_simultaneous_builds_all_see_the_same_profile(hist):
    vs.add_sample(hist.conn, "Shared sample text.")
    approved(hist, "Shared exemplar text.")
    expected = vp.build(hist)
    vp.invalidate()
    barrier = threading.Barrier(THREADS)
    results: list[str] = []
    errors: list[BaseException] = []

    def worker():
        try:
            barrier.wait(timeout=JOIN_TIMEOUT_SECONDS)
            results.append(vp.build(hist))
        except BaseException as e:      # surfaced by the assert below
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN_TIMEOUT_SECONDS)
    assert not any(t.is_alive() for t in threads), "a build deadlocked"
    assert errors == []
    assert results == [expected] * THREADS


def test_builds_racing_invalidations_never_return_a_partial_profile(hist):
    vs.add_sample(hist.conn, "Stable sample text.")
    expected = vp.build(hist)
    stop = threading.Event()
    results: list[str] = []
    errors: list[BaseException] = []

    def builder():
        try:
            for _ in range(50):
                results.append(vp.build(hist))
        except BaseException as e:      # surfaced by the assert below
            errors.append(e)

    def invalidator():
        while not stop.is_set():
            vp.invalidate()
            stop.wait(0.001)       # yield, so the builders are not starved

    flippers = [threading.Thread(target=invalidator) for _ in range(3)]
    builders = [threading.Thread(target=builder) for _ in range(4)]
    for t in flippers + builders:
        t.start()
    for t in builders:
        t.join(timeout=JOIN_TIMEOUT_SECONDS)
    stop.set()
    for t in flippers:
        t.join(timeout=JOIN_TIMEOUT_SECONDS)
    assert not any(t.is_alive() for t in flippers + builders)
    assert errors == []
    assert set(results) == {expected}
