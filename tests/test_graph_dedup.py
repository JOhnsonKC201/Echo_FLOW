"""Tests for duplicate-dictation collapsing in build_dictation_graph.

Identical dictations should render as a single node with a `count`, not N
overlapping nodes — the visual bug the user reported in the graph.
"""
from __future__ import annotations

import numpy as np

from src.graph import build_dictation_graph, _dedup_key


def _row(rid, ts, text, vec):
    v = np.asarray(vec, dtype=np.float32)
    v = v / (np.linalg.norm(v) or 1.0)  # L2-normalize (cosine via dot)
    return {"id": rid, "ts": ts, "style": "default",
            "raw": text, "cleaned": text, "vec": v, "quality": 80.0}


def test_identical_dictations_collapse_to_one_node():
    rows = [
        _row(1, 100, "Open Browser", [1.0, 0.0, 0.0]),
        _row(2, 200, "Open Browser", [1.0, 0.0, 0.0]),
        _row(3, 300, "Open Browser", [1.0, 0.0, 0.0]),
    ]
    g = build_dictation_graph(rows)
    assert len(g["nodes"]) == 1
    assert g["nodes"][0]["count"] == 3
    # Representative is the earliest occurrence.
    assert g["nodes"][0]["id"] == 1


def test_repeat_stutter_dupes_collapse_with_plain_form():
    # The stored-stutter form and the clean form share a dedup key.
    rows = [
        _row(1, 100, "Open Browser", [0.0, 1.0, 0.0]),
        _row(2, 200, "Open Browser Open Browser", [0.0, 1.0, 0.0]),
    ]
    g = build_dictation_graph(rows)
    assert len(g["nodes"]) == 1
    assert g["nodes"][0]["count"] == 2


def test_distinct_dictation_keeps_count_one():
    rows = [
        _row(1, 100, "Open Browser", [1.0, 0.0, 0.0]),
        _row(2, 200, "Close Tab", [0.0, 1.0, 0.0]),
    ]
    g = build_dictation_graph(rows)
    assert len(g["nodes"]) == 2
    assert all(node["count"] == 1 for node in g["nodes"])


def test_dedup_key_normalizes_case_punct_and_stutter():
    assert _dedup_key("Open Browser Open Browser") == _dedup_key("open browser")
    assert _dedup_key("Open Browser.") == _dedup_key("open browser")
