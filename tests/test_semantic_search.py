"""Tests for src/dashboard/semantic_search.py — cosine scan over stored embeddings.

The module reuses retrieval's storage format: each dictation row carries a
float32 BLOB (`numpy .tobytes()`) of an L2-normalized vector. We seed small
hand-built vectors (dimension doesn't matter to `_scan` — it only requires the
stored vector's dim to match the query's) so similarities are exact and the
ranking assertions are deterministic. `retrieval.embed` is monkeypatched in
every test that would touch it, because the real call downloads/loads a
sentence-transformers model — tests must stay offline.
"""
from __future__ import annotations

import numpy as np
import pytest

from src import retrieval
from src.history import History
from src.dashboard import semantic_search as ss


# --- helpers -------------------------------------------------------------------

def _h(tmp_path):
    return History(str(tmp_path / "h.db"))


def _vec(*components) -> np.ndarray:
    """L2-normalized float32 vector, matching what retrieval.embed produces."""
    v = np.asarray(components, dtype=np.float32)
    return v / np.linalg.norm(v)


def _seed(conn, cleaned: str, vec: np.ndarray | None, *,
          source: str = "desktop", ts: float = 1000.0,
          window: str = "Notepad", style: str = "default") -> int:
    """Insert a dictation row with an (optional) embedding blob; returns its id."""
    blob = retrieval.to_blob(vec) if vec is not None else None
    cur = conn.execute(
        "INSERT INTO dictations(ts, window_title, style, raw_text, cleaned_text, "
        "embedding, source) VALUES (?,?,?,?,?,?,?)",
        (ts, window, style, cleaned.lower(), cleaned, blob, source),
    )
    conn.commit()
    return cur.lastrowid


E1 = _vec(1, 0, 0, 0)            # the query direction in most tests
NEAR = _vec(1, 1, 0, 0)          # cos = ~0.7071 against E1
ORTHO = _vec(0, 1, 0, 0)         # cos = 0.0 against E1


# --- search_text ----------------------------------------------------------------

def test_search_text_ranks_results_by_descending_similarity(tmp_path, monkeypatch):
    """The whole point of semantic search: closest meaning first. Exact-match
    vector must outrank the 45-degree neighbor; the orthogonal row falls below
    the default min_sim=0.25 and is dropped entirely."""
    h = _h(tmp_path)
    id_exact = _seed(h.conn, "exact match", E1)
    id_near = _seed(h.conn, "near match", NEAR)
    _seed(h.conn, "unrelated", ORTHO)
    monkeypatch.setattr(retrieval, "embed", lambda q: E1)

    results = ss.search_text(h.conn, "anything")

    assert [r["id"] for r in results] == [id_exact, id_near]
    assert results[0]["similarity"] == pytest.approx(1.0, abs=1e-3)
    assert results[1]["similarity"] == pytest.approx(0.7071, abs=1e-3)
    # Sorted descending — the contract the UI relies on.
    sims = [r["similarity"] for r in results]
    assert sims == sorted(sims, reverse=True)


def test_search_text_result_shape_carries_display_fields(tmp_path, monkeypatch):
    """The dashboard renders these keys directly; a missing one breaks the page."""
    h = _h(tmp_path)
    _seed(h.conn, "Hello world.", E1, window="VS Code", style="email")
    monkeypatch.setattr(retrieval, "embed", lambda q: E1)

    (r,) = ss.search_text(h.conn, "hello")

    assert r["text"] == "Hello world."
    assert r["window_title"] == "VS Code"
    assert r["style"] == "email"
    assert isinstance(r["ts_human"], str) and r["ts_human"]


def test_search_text_blank_query_returns_empty_without_embedding(tmp_path, monkeypatch):
    """Whitespace-only queries short-circuit before the (expensive) model call."""
    h = _h(tmp_path)
    _seed(h.conn, "something", E1)

    def _boom(q):
        raise AssertionError("embed must not be called for a blank query")
    monkeypatch.setattr(retrieval, "embed", _boom)

    assert ss.search_text(h.conn, "   ") == []
    assert ss.search_text(h.conn, "") == []


def test_search_text_graceful_empty_when_model_unavailable(tmp_path, monkeypatch):
    """If sentence-transformers isn't installed (or the model fails to load),
    search must degrade to 'no results', never crash the dashboard."""
    h = _h(tmp_path)
    _seed(h.conn, "something", E1)
    monkeypatch.setattr(
        retrieval, "embed",
        lambda q: (_ for _ in ()).throw(RuntimeError("no model")),
    )

    assert ss.search_text(h.conn, "hello") == []


def test_search_text_empty_when_no_rows_have_embeddings(tmp_path, monkeypatch):
    """Rows without embedding blobs (pre-backfill DB) are simply invisible."""
    h = _h(tmp_path)
    _seed(h.conn, "no vector yet", None)
    monkeypatch.setattr(retrieval, "embed", lambda q: E1)

    assert ss.search_text(h.conn, "hello") == []


def test_search_text_excludes_mobile_rows_unless_trusted(tmp_path, monkeypatch):
    """Defense-in-depth: untrusted mobile-bridge rows must not surface in
    desktop search by default, mirroring RAG's posture."""
    h = _h(tmp_path)
    _seed(h.conn, "desktop note", E1, source="desktop")
    mob = _seed(h.conn, "mobile note", E1, source="mobile")
    monkeypatch.setattr(retrieval, "embed", lambda q: E1)

    default = ss.search_text(h.conn, "note")
    trusted = ss.search_text(h.conn, "note", trust_mobile=True)

    assert all(r["text"] != "mobile note" for r in default)
    assert mob in [r["id"] for r in trusted]


def test_search_text_skips_stale_short_blobs(tmp_path, monkeypatch):
    """A blob from an older/different model has the wrong dimension; the scan
    must skip it rather than crash on the matmul."""
    h = _h(tmp_path)
    good = _seed(h.conn, "good row", E1)
    _seed(h.conn, "stale row", np.asarray([1.0, 0.0], dtype=np.float32))  # 2-dim
    monkeypatch.setattr(retrieval, "embed", lambda q: E1)

    results = ss.search_text(h.conn, "hello")

    assert [r["id"] for r in results] == [good]


def test_search_text_respects_limit(tmp_path, monkeypatch):
    """`limit` caps the result set even when more rows clear min_sim."""
    h = _h(tmp_path)
    for i in range(5):
        _seed(h.conn, f"row {i}", E1)
    monkeypatch.setattr(retrieval, "embed", lambda q: E1)

    assert len(ss.search_text(h.conn, "hello", limit=2)) == 2


# --- similar_to_id --------------------------------------------------------------

def test_similar_to_id_excludes_self_and_ranks_neighbors(tmp_path):
    """'Find similar' uses the stored blob (no re-embed) and never returns the
    anchor row itself."""
    h = _h(tmp_path)
    anchor = _seed(h.conn, "anchor", E1)
    near = _seed(h.conn, "near", NEAR)
    far = _seed(h.conn, "far", ORTHO)

    results = ss.similar_to_id(h.conn, anchor)

    ids = [r["id"] for r in results]
    assert anchor not in ids
    # min_sim defaults to 0.0 so the orthogonal row (cos == 0.0) still appears.
    assert ids == [near, far]


def test_similar_to_id_unknown_id_returns_empty(tmp_path):
    """A deleted/never-existing id is a routine state, not an error."""
    h = _h(tmp_path)
    _seed(h.conn, "some row", E1)

    assert ss.similar_to_id(h.conn, 99999) == []


def test_similar_to_id_row_without_embedding_returns_empty(tmp_path):
    """An anchor that was never embedded can't have neighbors."""
    h = _h(tmp_path)
    rid = _seed(h.conn, "no vector", None)

    assert ss.similar_to_id(h.conn, rid) == []


# --- /search/api route (src/dashboard/app.py) -----------------------------------

class _App:
    def __init__(self, history):
        self.cfg = {"dashboard": {"host": "127.0.0.1", "port": 8766}}
        self.history = history
        self.reload_calls = 0

    def reload_config(self):
        self.reload_calls += 1


HOST = {"Host": "127.0.0.1:8766"}


def _client(tmp_path):
    from src.dashboard.app import make_app
    h = _h(tmp_path)
    app_ref = _App(h)
    return make_app(app_ref).test_client(), h


def test_search_api_q_returns_ranked_json(tmp_path, monkeypatch):
    """End-to-end through Flask: ?q= embeds the query and returns ranked rows."""
    client, h = _client(tmp_path)
    id_exact = _seed(h.conn, "exact", E1)
    id_near = _seed(h.conn, "near", NEAR)
    monkeypatch.setattr(retrieval, "embed", lambda q: E1)

    r = client.get("/search/api?q=hello", headers=HOST)

    assert r.status_code == 200
    results = r.get_json()["results"]
    assert [row["id"] for row in results] == [id_exact, id_near]


def test_search_api_like_returns_neighbors_excluding_anchor(tmp_path):
    """?like=<id> is the 'Find similar' affordance — stored-blob lookup, no
    embedding model needed at all."""
    client, h = _client(tmp_path)
    anchor = _seed(h.conn, "anchor", E1)
    near = _seed(h.conn, "near", NEAR)

    r = client.get(f"/search/api?like={anchor}", headers=HOST)

    assert r.status_code == 200
    ids = [row["id"] for row in r.get_json()["results"]]
    assert anchor not in ids
    assert near in ids


def test_search_api_blank_q_returns_empty_results(tmp_path, monkeypatch):
    """An empty query box must yield an empty (but well-formed) JSON envelope."""
    client, _h_ = _client(tmp_path)

    def _boom(q):
        raise AssertionError("embed must not be called for a blank query")
    monkeypatch.setattr(retrieval, "embed", _boom)

    r = client.get("/search/api?q=", headers=HOST)

    assert r.status_code == 200
    assert r.get_json() == {"results": []}


def test_search_api_survives_search_failure(tmp_path, monkeypatch):
    """Route wraps the search call in try/except — a blown model load becomes
    an empty result set, not a 500."""
    client, h = _client(tmp_path)
    _seed(h.conn, "row", E1)
    monkeypatch.setattr(
        retrieval, "embed",
        lambda q: (_ for _ in ()).throw(RuntimeError("model exploded")),
    )

    r = client.get("/search/api?q=hello", headers=HOST)

    assert r.status_code == 200
    assert r.get_json() == {"results": []}
