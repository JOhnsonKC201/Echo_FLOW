"""Tests for src/graph.py — build_notes_graph, cluster labeling, render_graph.

Seeds a real History DB (notes + tags schema) the same way test_notes.py does,
then asserts on the node/edge structure of the Notes view and on the rendered
HTML. XSS coverage mirrors tests/test_viewer_xss.py: every interpolated field
in an innerHTML sink must route through the template's escape() helper.
"""
from __future__ import annotations

import json

import pytest

from src.graph import _label_clusters, _load_rows, build_notes_graph, render_graph
from src.history import History
from src.notes import promote_to_note


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_dictation(h: History, ts: float, text: str) -> int:
    cur = h.conn.execute(
        "INSERT INTO dictations(ts, raw_text, cleaned_text) VALUES (?, ?, ?)",
        (ts, text, text),
    )
    h.conn.commit()
    return cur.lastrowid


def _make_db(tmp_path) -> tuple[History, str]:
    db = str(tmp_path / "h.db")
    return History(db), db


def _nodes_by_id(graph: dict) -> dict:
    return {n["id"]: n for n in graph["nodes"]}


# ---------------------------------------------------------------------------
# build_notes_graph — node and edge structure
# ---------------------------------------------------------------------------

def test_notes_graph_links_note_to_source_dictation(tmp_path):
    h, db = _make_db(tmp_path)
    d1 = _seed_dictation(h, 100.0, "I built a knowledge graph for dictations.")
    d2 = _seed_dictation(h, 200.0, "Totally unrelated grocery list item.")
    nid = promote_to_note(h, d1, title="Knowledge graph idea", description="big deal")
    rows = _load_rows(db)
    h.conn.close()

    g = build_notes_graph(db, rows)

    assert g["kind"] == "notes"
    nodes = _nodes_by_id(g)
    # The note is a primary node carrying its metadata.
    note = nodes[f"note:{nid}"]
    assert note["kind"] == "note"
    assert note["label"] == "Knowledge graph idea"
    assert note["full"] == "big deal"
    assert note["dictation_id"] == d1
    # Its source dictation appears as a secondary node...
    assert nodes[f"dict:{d1}"]["kind"] == "dictation"
    # ...but the unlinked dictation does not.
    assert f"dict:{d2}" not in nodes
    # Exactly one edge: note -> source dictation, with the fixed 0.9 weight.
    assert g["links"] == [{
        "source": f"note:{nid}",
        "target": f"dict:{d1}",
        "value": 0.9,
        "ts": note["ts"],
    }]


def test_notes_graph_includes_only_confirmed_tags(tmp_path):
    h, db = _make_db(tmp_path)
    did = _seed_dictation(h, 100.0, "Tagged dictation about deployments.")
    nid = promote_to_note(h, did, title="Deploy note")
    h.set_tag(did, "infra", confirmed=True)
    h.set_tag(did, "speculative", source="auto", confidence=0.4, confirmed=False)
    rows = _load_rows(db)
    h.conn.close()

    g = build_notes_graph(db, rows)
    note = _nodes_by_id(g)[f"note:{nid}"]
    assert note["tags"] == ["infra"]


def test_notes_graph_truncates_long_dictation_labels(tmp_path):
    h, db = _make_db(tmp_path)
    long_text = "this dictation is much longer than forty characters and keeps going"
    did = _seed_dictation(h, 100.0, long_text)
    promote_to_note(h, did, title="Long one")
    rows = _load_rows(db)
    h.conn.close()

    g = build_notes_graph(db, rows)
    d_node = _nodes_by_id(g)[f"dict:{did}"]
    assert d_node["label"].endswith("…")
    assert len(d_node["label"]) <= 41  # 40 chars + ellipsis
    assert d_node["full"] == long_text  # full text preserved for the panel


def test_notes_graph_note_without_loaded_dictation_has_no_edge(tmp_path):
    """A note whose source dictation isn't in `rows` (filtered out) still
    renders as a node — just with no edge and no dangling dictation node."""
    h, db = _make_db(tmp_path)
    did = _seed_dictation(h, 100.0, "ephemeral dictation")
    nid = promote_to_note(h, did, title="Orphaned note")
    h.conn.close()

    g = build_notes_graph(db, rows=[])  # dictation not loaded
    nodes = _nodes_by_id(g)
    assert f"note:{nid}" in nodes
    assert f"dict:{did}" not in nodes
    assert g["links"] == []


def test_notes_graph_empty_when_db_missing_or_no_notes(tmp_path):
    # Missing DB file
    g = build_notes_graph(str(tmp_path / "nope.db"), rows=[])
    assert g == {"nodes": [], "links": [], "kind": "notes"}
    # DB exists but has no notes
    h, db = _make_db(tmp_path)
    _seed_dictation(h, 100.0, "a dictation with no note")
    rows = _load_rows(db)
    h.conn.close()
    g2 = build_notes_graph(db, rows)
    assert g2 == {"nodes": [], "links": [], "kind": "notes"}


# ---------------------------------------------------------------------------
# Cluster labeling helper (_label_clusters) — small-fixture fallback path
# ---------------------------------------------------------------------------

def _crow(text: str) -> dict:
    return {"cleaned": text, "raw": text}


def test_label_clusters_picks_distinctive_concepts():
    # < 30 rows → deterministic frequency-fallback path (no TF-IDF).
    rows = [
        _crow("notes about Python and Django stuff"),
        _crow("more notes about Python and Django here"),
        _crow("thinking about Python and Django again"),
        _crow("met Alice today"),
    ]
    clusters = [0, 0, 0, 1]
    labels = _label_clusters(rows, clusters)
    assert "Python" in labels[0]
    assert "Django" in labels[0]
    # Cluster 1 has fewer than 3 concept tokens → no label.
    assert labels[1] == ""


def test_label_clusters_empty_inputs():
    assert _label_clusters([], []) == {}


def test_label_clusters_ignores_stop_words():
    rows = [
        _crow("Yeah Okay Yesterday whatever"),
        _crow("Yeah Okay Yesterday whatever"),
        _crow("Yeah Okay Yesterday whatever"),
    ]
    labels = _label_clusters(rows, [0, 0, 0])
    # All capitalized tokens are stop/filler words → no concepts at all,
    # so the cluster never gets an entry (or an empty one).
    assert labels.get(0, "") == ""


# ---------------------------------------------------------------------------
# render_graph — output file + XSS posture
# ---------------------------------------------------------------------------

_XSS_TITLE = "</script><script>alert(1)</script>"


def _render(tmp_path, title: str) -> str:
    h, db = _make_db(tmp_path)
    did = _seed_dictation(h, 100.0, "a dictation worth pinning")
    promote_to_note(h, did, title=title, description="desc")
    h.conn.close()
    out = render_graph(db, out_path=str(tmp_path / "graph.html"),
                       open_browser=False)
    return open(out, encoding="utf-8").read()


def test_render_graph_writes_file_with_data_substituted(tmp_path):
    html = _render(tmp_path, "Pinned idea")
    # Every placeholder must be replaced with real JSON.
    for placeholder in ("__DICT_DATA__", "__CONCEPT_DATA__", "__NOTES_DATA__",
                        "__TAG_USAGE__", "__TAGS_INDEX__", "__TS_MIN__", "__TS_MAX__"):
        assert placeholder not in html
    assert json.dumps("Pinned idea") in html  # note title embedded in NOTES JSON


def test_panel_sinks_route_through_escape_helper(tmp_path):
    """Mirror of test_viewer_xss: every field interpolated into an innerHTML
    sink must go through the template's escape() helper — no bare ${d.x}."""
    html = _render(tmp_path, "Pinned idea")
    # Escaped sinks present...
    assert '<pre>${escape(d.raw || "")}</pre>' in html
    assert "<pre>${escape(d.full)}</pre>" in html
    assert '<pre>${escape(d.style || "")}</pre>' in html
    assert "<pre>${escape(d.label)}</pre>" in html
    assert "${escape(t)}" in html  # tag chips
    # ...and no bare equivalents remain.
    assert "<pre>${d.raw" not in html
    assert "<pre>${d.full" not in html
    assert "<pre>${d.style" not in html
    assert "<pre>${d.label" not in html


def test_note_title_cannot_break_out_of_script_block(tmp_path):
    """Regression: render_graph embeds note titles/dictation text into an
    inline <script> block via json.dumps, which does not escape '</script>'.
    Before the fix, a note titled '</script><script>alert(1)</script>'
    terminated the script element and injected a live <script> — stored XSS
    in graph.html. The JSON now escapes '</' as '<\\/'."""
    html = _render(tmp_path, _XSS_TITLE)
    assert _XSS_TITLE not in html, "raw </script> payload must not survive into the HTML"
    # The payload still round-trips as data, just with the JS-safe escape.
    assert "<\\/script>" in html
