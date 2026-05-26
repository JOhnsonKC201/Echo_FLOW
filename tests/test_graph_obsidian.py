"""Tests for src.dashboard.graph_obsidian — id namespacing, edge remapping,
and the script-injection-safe render path."""
from __future__ import annotations

import json
import re

import pytest


# --- _merge: unit -----------------------------------------------------------

def test_merge_empty_inputs_returns_empty_lists():
    from src.dashboard.graph_obsidian import _merge
    out = _merge({}, {}, {})
    assert out == {"nodes": [], "links": []}


def test_merge_namespaces_dictation_ids():
    from src.dashboard.graph_obsidian import _merge
    dict_g = {
        "nodes": [{"id": 1, "label": "hello", "cluster": 0},
                  {"id": 2, "label": "world", "cluster": 1}],
        "links": [{"source": 1, "target": 2, "value": 0.8}],
    }
    out = _merge(dict_g, {}, {})
    ids = [n["id"] for n in out["nodes"]]
    assert "d:1" in ids and "d:2" in ids
    link = out["links"][0]
    assert link["source"] == "d:1"
    assert link["target"] == "d:2"
    assert link["kind"] == "sim"
    assert pytest.approx(link["value"]) == 0.8


def test_merge_concept_size_scales_with_freq():
    from src.dashboard.graph_obsidian import _merge
    concept_g = {
        "nodes": [
            {"id": "python", "label": "python", "freq": 1},
            {"id": "rust",   "label": "rust",   "freq": 5},
            {"id": "go",     "label": "go",     "freq": 999},  # capped at +10
        ],
        "links": [{"source": "python", "target": "rust"}],
    }
    out = _merge({}, concept_g, {})
    by_id = {n["id"]: n for n in out["nodes"]}
    # Concept namespace.
    assert "c:python" in by_id and "c:rust" in by_id
    # size = 6 + min(10, freq)
    assert by_id["c:python"]["size"] == 7
    assert by_id["c:rust"]["size"] == 11
    assert by_id["c:go"]["size"] == 16  # capped
    # All concepts carry the freq field through.
    assert by_id["c:go"]["freq"] == 999
    # Cooc edges get the concept namespace + 'cooc' kind.
    e = out["links"][0]
    assert e["source"] == "c:python" and e["target"] == "c:rust"
    assert e["kind"] == "cooc"


def test_merge_notes_edge_remapping_note_to_dict():
    """notes_g uses 'note:X' / 'dict:Y'; _merge must rewrite to 'n:X' / 'd:Y'."""
    from src.dashboard.graph_obsidian import _merge
    notes_g = {
        "nodes": [{"id": "abc", "label": "My Note"}],
        "links": [{"source": "note:abc", "target": "dict:42", "value": 0.9}],
    }
    out = _merge({}, {}, notes_g)
    # Note node lives in n: namespace.
    assert out["nodes"][0]["id"] == "n:abc"
    assert out["nodes"][0]["group"] == "note"
    # Edge endpoints rewritten.
    e = out["links"][0]
    assert e["source"] == "n:abc"
    assert e["target"] == "d:42"
    assert e["kind"] == "note"


def test_merge_skips_duplicate_dictation_nodes_in_notes_graph():
    """notes_graph re-emits source dictations as nodes with kind=dictation;
    _merge should drop those to avoid duplicating d: entries."""
    from src.dashboard.graph_obsidian import _merge
    notes_g = {
        "nodes": [
            {"id": "5", "kind": "dictation", "label": "dup"},   # must be skipped
            {"id": "x", "label": "real note"},                  # must be kept
        ],
        "links": [],
    }
    out = _merge({}, {}, notes_g)
    ids = [n["id"] for n in out["nodes"]]
    assert "n:x" in ids
    assert all(not (i.startswith("n:") and i.endswith(":5")) for i in ids)


# --- render: integration ----------------------------------------------------

def _seed_db(tmp_path, n=4):
    """Create a History DB with n dictations (no embeddings — graph still renders)."""
    from src.history import History
    db_path = tmp_path / "history.db"
    h = History(str(db_path))
    for i in range(n):
        h.log(
            window_title=f"win-{i}",
            style="default",
            language="en",
            duration_ms=1000,
            raw_text=f"raw dictation number {i}",
            cleaned_text=f"Cleaned dictation number {i}.",
            quality_score=80.0,
        )
    h.conn.close()
    return str(db_path)


def test_render_returns_html_with_graph_data_script(tmp_path):
    from src.dashboard import graph_obsidian
    db_path = _seed_db(tmp_path, n=4)
    html = graph_obsidian.render(db_path)
    assert isinstance(html, str)
    assert html.lstrip().lower().startswith("<!doctype html>")
    # Marker tag MUST be present so the client-side JS can find data.
    assert '__graph_data' in html
    assert 'type="application/json"' in html
    assert '<svg id="g"></svg>' in html


def test_render_escapes_closing_script_tag(tmp_path, monkeypatch):
    """A malicious label containing </script> must not be able to break out of
    the JSON data island into executable JS context."""
    from src.dashboard import graph_obsidian

    poisoned = {
        "nodes": [{"id": "x", "label": "</script><img src=x onerror=alert(1)>",
                   "group": "concept", "cluster": -1, "size": 7}],
        "links": [],
    }
    monkeypatch.setattr(graph_obsidian, "_merge",
                        lambda *a, **k: poisoned)
    # _load_rows etc. still run but the merged result is the poisoned one.
    db_path = _seed_db(tmp_path, n=1)
    html = graph_obsidian.render(db_path)
    # The escaped form must appear; the raw </script> must NOT appear inside
    # the embedded JSON payload.
    # Pull out the JSON between the start of __graph_data block and the script tag for d3.
    m = re.search(
        r'<script id="__graph_data" type="application/json">(.*?)</script>',
        html, re.DOTALL,
    )
    assert m, "graph data script block not found"
    payload = m.group(1)
    # Belt-and-suspenders: `</` is escaped to `<\/` in the payload.
    assert "</script>" not in payload
    assert "<\\/script>" in payload or "<\\/" in payload
    # And the payload is still valid JSON once we un-escape.
    data = json.loads(payload.replace("<\\/", "</"))
    assert any(n["id"] == "x" for n in data["nodes"])
