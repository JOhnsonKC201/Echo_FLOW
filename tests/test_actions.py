"""Tests for src/actions.py — action item extraction."""
from __future__ import annotations

from src.actions import extract_action_items


def test_simple_todo_prefix():
    items = extract_action_items("TODO: review the auth PR before noon.")
    assert items == ["review the auth PR before noon"]


def test_remind_me_to():
    items = extract_action_items("Remind me to email Sarah about the budget.")
    assert items == ["email Sarah about the budget"]


def test_i_need_to():
    items = extract_action_items("I need to fix the login bug today.")
    assert items == ["fix the login bug today"]


def test_lets_prefix():
    items = extract_action_items("Let's schedule a meeting with the design team.")
    # The leading verb "schedule" should be retained inside the captured text.
    assert any("schedule a meeting" in i for i in items)


def test_blocklist_excludes_daily_drivel():
    """'I need to go to bed' should NOT be a TODO."""
    assert extract_action_items("I need to go to bed.") == []
    assert extract_action_items("I have to eat lunch.") == []


def test_multiple_actions_in_one_dictation():
    text = "TODO: fix the layout. Also remind me to call John tomorrow."
    items = extract_action_items(text)
    assert len(items) >= 2
    assert any("fix the layout" in i for i in items)
    assert any("call John" in i for i in items)


def test_dedupe_repeats():
    text = "I need to ship this. I need to ship this."
    items = extract_action_items(text)
    assert items == ["ship this"]


def test_short_or_empty_input_returns_empty():
    assert extract_action_items("") == []
    assert extract_action_items("hi") == []


def test_no_action_words_returns_empty():
    """Pure descriptive text shouldn't trigger anything."""
    assert extract_action_items("The weather is nice today.") == []


def test_trailing_conjunction_trimmed():
    items = extract_action_items("I need to fix the build and")
    # Should NOT end with 'and'
    assert items
    assert not items[0].lower().endswith(" and")
