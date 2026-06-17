"""Tests for src/history.py action-items API — the dashboard's lightweight todo log.

Action items are extracted from dictations ("remind me to...") and surfaced on
the dashboard until completed. The contract under test: add → visible in both
open_action_items (global view) and action_items_for_dictation (per-row view);
mark complete → drops out of the open list but stays in the per-dictation
history with a completion timestamp.
"""
from __future__ import annotations

from src.history import History


def _h(tmp_path):
    return History(str(tmp_path / "h.db"))


def test_add_returns_positive_rowid(tmp_path):
    """Callers stash the returned id to mark completion later — 0 would mean
    the insert silently failed."""
    h = _h(tmp_path)

    aid = h.add_action_item(1, "send the report")

    assert isinstance(aid, int) and aid > 0


def test_round_trip_add_then_complete(tmp_path):
    """The full lifecycle: a new item is open everywhere, a completed item is
    excluded from the open list but preserved (with completed_at) in the
    per-dictation history."""
    h = _h(tmp_path)
    aid = h.add_action_item(7, "email the team")

    # Freshly added → present in both views.
    open_ids = [row[0] for row in h.open_action_items()]
    assert aid in open_ids
    items = h.action_items_for_dictation(7)
    assert len(items) == 1
    item_id, text, completed, created_at, completed_at = items[0]
    assert (item_id, text, completed) == (aid, "email the team", 0)
    assert created_at > 0
    assert completed_at is None

    h.mark_action_complete(aid)

    # Completed → gone from the open list, retained per-dictation with a stamp.
    assert aid not in [row[0] for row in h.open_action_items()]
    item_id, text, completed, created_at, completed_at = h.action_items_for_dictation(7)[0]
    assert completed == 1
    assert completed_at is not None and completed_at >= created_at


def test_mark_action_complete_false_reopens_item(tmp_path):
    """Un-completing (the dashboard's undo) must clear completed_at, not just
    flip the flag — a stale timestamp would lie about when it was done."""
    h = _h(tmp_path)
    aid = h.add_action_item(1, "buy milk")
    h.mark_action_complete(aid)

    h.mark_action_complete(aid, completed=False)

    assert aid in [row[0] for row in h.open_action_items()]
    _, _, completed, _, completed_at = h.action_items_for_dictation(1)[0]
    assert completed == 0
    assert completed_at is None


def test_add_with_null_dictation_id_still_appears_open(tmp_path):
    """dictation_id is nullable — manually added todos have no source row but
    must still show up on the dashboard's open list."""
    h = _h(tmp_path)

    aid = h.add_action_item(None, "standalone todo")

    rows = h.open_action_items()
    match = [r for r in rows if r[0] == aid]
    assert len(match) == 1
    _id, dictation_id, text, _created = match[0]
    assert dictation_id is None
    assert text == "standalone todo"


def test_action_items_for_dictation_filters_by_dictation(tmp_path):
    """Each dictation's detail view shows only its own items, ordered by id."""
    h = _h(tmp_path)
    a1 = h.add_action_item(1, "first for d1")
    h.add_action_item(2, "for d2")
    a2 = h.add_action_item(1, "second for d1")

    items = h.action_items_for_dictation(1)

    assert [row[0] for row in items] == [a1, a2]
    assert [row[1] for row in items] == ["first for d1", "second for d1"]


def test_open_action_items_respects_limit(tmp_path):
    """The dashboard asks for a bounded page; an unbounded list would grow
    forever on a chatty user."""
    h = _h(tmp_path)
    for i in range(5):
        h.add_action_item(1, f"item {i}")

    assert len(h.open_action_items(limit=3)) == 3
    assert len(h.open_action_items()) == 5


def test_open_action_items_only_lists_incomplete(tmp_path):
    """Mixed open/done state: only the open ones survive the filter."""
    h = _h(tmp_path)
    a_open = h.add_action_item(1, "still open")
    a_done = h.add_action_item(1, "already done")
    h.mark_action_complete(a_done)

    ids = [row[0] for row in h.open_action_items()]

    assert a_open in ids
    assert a_done not in ids


def test_open_action_items_breaks_created_at_ties_by_id(tmp_path):
    """Items extracted within the same second share a created_at; without an id
    tiebreaker SQLite's order among ties is unspecified and the dashboard list
    would shuffle between requests. Newest id first is the documented order."""
    h = _h(tmp_path)
    a1 = h.add_action_item(1, "first")
    a2 = h.add_action_item(1, "second")
    a3 = h.add_action_item(1, "third")
    # Force identical timestamps so created_at alone cannot order them.
    h.conn.execute("UPDATE action_items SET created_at = 1000.0")
    h.conn.commit()

    ids = [row[0] for row in h.open_action_items()]

    assert ids == [a3, a2, a1]
