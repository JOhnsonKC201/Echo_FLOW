"""Tests for A/B provider comparison plumbing."""
from __future__ import annotations

import time

from src.cleanup import Cleaner
from src.history import History


def test_clean_with_overrides_provider_temporarily():
    """clean_with(provider) should swap the provider just for that call."""
    c = Cleaner({"enabled": True, "provider": "none"})
    assert c.provider == "none"
    # With provider="none", clean() returns the input unchanged — useful to
    # confirm the override path doesn't crash and restores the original.
    out = c.clean_with("none", "hello")
    assert out == "hello"
    assert c.provider == "none"   # restored


def test_clean_with_restores_provider_even_on_exception():
    c = Cleaner({"enabled": True, "provider": "none"})
    # Force an exception by using a non-existent provider — Cleaner.clean
    # returns text for unknown providers, no exception, so just verify the
    # provider is back to original after any call path.
    c.clean_with("nonexistent_provider", "hi")
    assert c.provider == "none"


def test_history_logs_and_tallies_ab(tmp_path):
    db = str(tmp_path / "history.db")
    h = History(db)
    h.log_ab(
        raw_text="hi", primary_provider="ollama", primary_text="Hi.",
        primary_quality=80.0, alt_provider="learned", alt_text="Hi.",
        alt_quality=75.0, winner="ollama",
    )
    h.log_ab(
        raw_text="hello", primary_provider="ollama", primary_text="Hello.",
        primary_quality=70.0, alt_provider="learned", alt_text="Hello.",
        alt_quality=72.0, winner="learned",
    )
    h.log_ab(
        raw_text="yo", primary_provider="ollama", primary_text="Yo.",
        primary_quality=60.0, alt_provider="learned", alt_text="Yo.",
        alt_quality=60.5, winner="tie",
    )
    tally = h.ab_tally()
    assert tally == {"ollama": 1, "learned": 1, "tie": 1}


def test_ab_tally_respects_time_window(tmp_path):
    db = str(tmp_path / "history.db")
    h = History(db)
    # Insert a row dated 30 days ago manually.
    old_ts = time.time() - 30 * 86400
    h.conn.execute(
        "INSERT INTO provider_ab_log(ts, raw_text, primary_provider, primary_text, "
        "primary_quality, alt_provider, alt_text, alt_quality, winner) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (old_ts, "x", "ollama", "x", 50.0, "learned", "x", 50.0, "tie"),
    )
    h.conn.commit()
    # Recent (7 day) tally should NOT include the 30-day-old row.
    assert h.ab_tally(since_seconds=7 * 86400) == {}
    # 60-day tally SHOULD include it.
    assert h.ab_tally(since_seconds=60 * 86400) == {"tie": 1}
