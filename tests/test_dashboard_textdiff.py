"""Word-level diff used to show what the humanizer changed.

Contract: opcode tuples only — never markup, since the template owns escaping.
Rejoining every chunk must reproduce the two inputs exactly, or the diff would
silently misrepresent the rewrite it is meant to make auditable.
"""
from __future__ import annotations

import pytest

from src.dashboard.textdiff import word_diff, change_ratio


def _rebuild(diff, side):
    keep = ("equal", "delete") if side == "before" else ("equal", "insert")
    return "".join(c for op, c in diff if op in keep)


@pytest.mark.parametrize("before, after", [
    ("The quick brown fox", "The slow brown fox"),
    ("Moreover, it is a testament to our robust culture.",
     "It says a lot about how we build things."),
    ("one\n\ntwo", "one changed\n\ntwo"),
    ("same text", "same text"),
    ("", "something new"),
    ("something old", ""),
])
def test_diff_is_lossless(before, after):
    """The two sides must reconstruct exactly — including whitespace, which is
    tokenized rather than discarded."""
    diff = word_diff(before, after)
    assert _rebuild(diff, "before") == before
    assert _rebuild(diff, "after") == after


def test_diff_marks_only_the_changed_words():
    diff = word_diff("the quick brown fox", "the slow brown fox")
    assert ("delete", "quick") in diff
    assert ("insert", "slow") in diff
    # "brown fox" is untouched and must not be re-emitted as a change.
    assert not any(op != "equal" and "brown" in chunk for op, chunk in diff)


@pytest.mark.parametrize("before, after", [
    ("a b c d", "x y z w"),
    ("one two three four five", "one changed three modified five"),
    ("Moreover, it is a testament to our robust culture.",
     "It says a lot about how we build things."),
])
def test_no_two_adjacent_chunks_share_an_op(before, after):
    """Runs are merged, so a paragraph rewrite renders as a handful of spans
    rather than one element per word. Whitespace legitimately matches on both
    sides, so equal chunks still interleave — that is correct, not a failure
    to merge."""
    ops = [op for op, _ in word_diff(before, after)]
    assert all(a != b for a, b in zip(ops, ops[1:]))


def test_a_pure_replacement_reads_as_delete_then_insert():
    """Order matters for rendering: strikethrough first, replacement after."""
    assert word_diff("alpha", "omega") == [("delete", "alpha"), ("insert", "omega")]


def test_identical_text_is_all_equal():
    diff = word_diff("nothing changed here", "nothing changed here")
    assert {op for op, _ in diff} == {"equal"}
    assert change_ratio("nothing changed here", "nothing changed here") == 0.0


def test_change_ratio_is_bounded_and_directional():
    assert change_ratio("", "anything") == 0.0          # no baseline to change
    assert change_ratio("a b c d", "a b c d") == 0.0
    full = change_ratio("alpha beta gamma", "delta epsilon zeta")
    assert full == pytest.approx(1.0)
    half = change_ratio("alpha beta gamma delta", "alpha beta zeta omega")
    assert 0.0 < half < 1.0


def test_diff_emits_no_markup():
    """The template escapes and styles; this must never produce HTML itself."""
    diff = word_diff("<b>bold</b> & co", "<i>italic</i> & co")
    for _, chunk in diff:
        assert "<span" not in chunk and "<del" not in chunk and "<ins" not in chunk
    assert _rebuild(diff, "before") == "<b>bold</b> & co"
