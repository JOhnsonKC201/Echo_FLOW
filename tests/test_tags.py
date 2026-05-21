"""Tests for src/tags.py — suggestion + persistence."""
from __future__ import annotations

import tempfile

from src.history import History
from src.tags import (
    suggest_tags, apply_suggestions, _normalize_tag_name, _cluster_signal,
    _concept_signal, TagSuggestion,
)


def test_normalize_tag_name_basic():
    assert _normalize_tag_name("Hello World") == "hello-world"
    assert _normalize_tag_name("  Echo Flow  ") == "echo-flow"
    assert _normalize_tag_name("UPPER_case") == "upper_case"
    assert _normalize_tag_name("with!punct$") == "with-punct"


def test_cluster_signal_splits_on_dot():
    sigs = _cluster_signal("Shift · Ctrl")
    names = [s.name for s in sigs]
    assert "shift" in names
    assert "ctrl" in names
    assert all(s.source == "cluster" for s in sigs)


def test_cluster_signal_empty():
    assert _cluster_signal(None) == []
    assert _cluster_signal("") == []


def test_concept_signal_matches_known_tag(tmp_path):
    known = {"echo-flow", "whisper"}
    sigs = _concept_signal("I love using EchoFlow with Whisper", known)
    names = {s.name for s in sigs}
    # "EchoFlow" → "echoflow" (no dash); "Whisper" → "whisper"
    # Only "whisper" matches known tags as-is.
    assert "whisper" in names


def test_apply_suggestions_persists_to_dictation_tags(tmp_path):
    db = str(tmp_path / "h.db")
    h = History(db)
    # Need a dictation row to FK against (even though FK is not enforced in sqlite by default)
    h.conn.execute(
        "INSERT INTO dictations(ts, raw_text, cleaned_text) VALUES (1, 'hello', 'Hello.')"
    )
    h.conn.commit()
    suggestions = [
        TagSuggestion(name="work", confidence=0.7, source="cluster"),
        TagSuggestion(name="meeting", confidence=0.6, source="similar"),
    ]
    n = apply_suggestions(h, 1, suggestions)
    assert n == 2
    tags = h.get_tags_for_dictation(1)
    assert len(tags) == 2
    names = {t[0] for t in tags}
    assert names == {"work", "meeting"}
    # All unconfirmed (confirmed=0)
    assert all(t[3] == 0 for t in tags)


def test_set_tag_manual_marks_confirmed(tmp_path):
    db = str(tmp_path / "h.db")
    h = History(db)
    h.conn.execute(
        "INSERT INTO dictations(ts, raw_text, cleaned_text) VALUES (1, 'hi', 'Hi.')"
    )
    h.conn.commit()
    h.set_tag(1, "important", source="manual", confidence=1.0, confirmed=True)
    tags = h.get_tags_for_dictation(1)
    assert tags[0][0] == "important"
    assert tags[0][3] == 1  # confirmed


def test_manual_tag_upgrades_existing_suggestion(tmp_path):
    db = str(tmp_path / "h.db")
    h = History(db)
    h.conn.execute(
        "INSERT INTO dictations(ts, raw_text, cleaned_text) VALUES (1, 'x', 'X.')"
    )
    h.conn.commit()
    # First as a suggestion
    h.set_tag(1, "auto", source="cluster", confidence=0.7, confirmed=False)
    # Then manual confirm
    h.set_tag(1, "auto", source="manual", confidence=1.0, confirmed=True)
    tags = h.get_tags_for_dictation(1)
    assert tags[0][3] == 1   # now confirmed


def test_remove_tag(tmp_path):
    db = str(tmp_path / "h.db")
    h = History(db)
    h.conn.execute(
        "INSERT INTO dictations(ts, raw_text, cleaned_text) VALUES (1, 'x', 'X.')"
    )
    h.conn.commit()
    h.set_tag(1, "todrop", confirmed=True)
    h.remove_tag(1, "todrop")
    assert h.get_tags_for_dictation(1) == []


def test_suggest_tags_with_no_signals_returns_empty(tmp_path):
    db = str(tmp_path / "h.db")
    h = History(db)
    # No retriever, no cluster label, no known tags → nothing to suggest.
    out = suggest_tags("hello world", retriever=None, history=h, cluster_label=None)
    assert out == []
