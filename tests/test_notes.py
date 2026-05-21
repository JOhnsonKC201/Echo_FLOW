"""Tests for src/notes.py — note promotion + backlinks."""
from __future__ import annotations

from src.history import History
from src.notes import (
    promote_to_note, list_notes, get_note, update_note, backlinks_for, _auto_title,
)


def _seed_dictation(h, text: str) -> int:
    cur = h.conn.execute(
        "INSERT INTO dictations(ts, raw_text, cleaned_text) VALUES (1, ?, ?)",
        (text, text),
    )
    h.conn.commit()
    return cur.lastrowid


def test_auto_title_uses_first_sentence():
    assert _auto_title("Hello world. This is a test.") == "Hello world"


def test_auto_title_clips_to_max_words():
    title = _auto_title("one two three four five six seven eight nine ten")
    assert title == "one two three four five six seven eight"


def test_auto_title_handles_empty():
    assert _auto_title("") == "Untitled"


def test_promote_to_note_creates_row(tmp_path):
    h = History(str(tmp_path / "h.db"))
    did = _seed_dictation(h, "I built a knowledge graph for my dictation app.")
    nid = promote_to_note(h, did)
    assert nid > 0
    note = get_note(h, nid)
    assert note is not None
    assert note.dictation_id == did
    assert "knowledge graph" in note.title


def test_promote_with_explicit_title(tmp_path):
    h = History(str(tmp_path / "h.db"))
    did = _seed_dictation(h, "blah")
    nid = promote_to_note(h, did, title="Important Idea", description="big deal")
    note = get_note(h, nid)
    assert note.title == "Important Idea"
    assert note.description == "big deal"


def test_list_notes_sorted_recent_first(tmp_path):
    h = History(str(tmp_path / "h.db"))
    d1 = _seed_dictation(h, "first")
    n1 = promote_to_note(h, d1, title="first")
    import time as _t
    _t.sleep(0.01)
    d2 = _seed_dictation(h, "second")
    n2 = promote_to_note(h, d2, title="second")
    notes = list_notes(h)
    assert notes[0].id == n2
    assert notes[1].id == n1


def test_update_note_changes_fields(tmp_path):
    h = History(str(tmp_path / "h.db"))
    did = _seed_dictation(h, "x")
    nid = promote_to_note(h, did, title="old")
    update_note(h, nid, title="new", description="now described")
    note = get_note(h, nid)
    assert note.title == "new"
    assert note.description == "now described"


def test_backlinks_lexical_match(tmp_path):
    """Title appearing in another dictation's cleaned_text → backlink."""
    h = History(str(tmp_path / "h.db"))
    d_src = _seed_dictation(h, "echo flow design")
    nid = promote_to_note(h, d_src, title="echo flow design")
    # Another dictation that mentions the title
    d2 = _seed_dictation(h, "I was thinking about the echo flow design yesterday.")
    # And one that does not
    _seed_dictation(h, "totally unrelated content here.")

    bl = backlinks_for(h, retriever=None, note_id=nid)
    ids = {b[0] for b in bl}
    assert d2 in ids
    # The source dictation itself is excluded
    assert d_src not in ids


def test_backlinks_word_boundary(tmp_path):
    """'design' inside 'designation' should NOT backlink to a 'design' note."""
    h = History(str(tmp_path / "h.db"))
    d_src = _seed_dictation(h, "design")
    nid = promote_to_note(h, d_src, title="design")
    d2 = _seed_dictation(h, "the designation was made yesterday")
    bl = backlinks_for(h, retriever=None, note_id=nid)
    ids = {b[0] for b in bl}
    assert d2 not in ids
