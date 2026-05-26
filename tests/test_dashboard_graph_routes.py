"""Routes /graph and /graph/raw, plus the mtime-keyed _graph_cache invariant.

The cache must:
  - serve a second identical request without re-rendering (single render() call)
  - bust when ?refresh=1 is passed
  - bust when the db file mtime changes
"""
from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import patch

import pytest


# --- helpers ---------------------------------------------------------------

def _app_ref(tmp_path):
    """Real History + a stub App namespace pointing at a fresh tmp db."""
    from src.history import History
    db_path = tmp_path / "history.db"
    h = History(str(db_path))
    h.log(
        window_title="w", style="default", language="en",
        duration_ms=100, raw_text="hello world", cleaned_text="Hello world.",
        quality_score=80.0,
    )
    cfg = {
        "dashboard": {"enabled": True, "host": "127.0.0.1",
                      "port": 8766, "theme": "dark"},
        "history": {"db_path": str(db_path)},
    }
    app_ref = types.SimpleNamespace(cfg=cfg, cfg_path="config.yaml", history=h)
    return app_ref, db_path


def _client(app_ref):
    from src.dashboard.app import make_app
    return make_app(app_ref).test_client()


HEADERS = {"Host": "127.0.0.1:8766"}


# --- /graph ----------------------------------------------------------------

def test_graph_view_renders_iframe_shell(tmp_path):
    app_ref, _ = _app_ref(tmp_path)
    client = _client(app_ref)
    r = client.get("/graph", headers=HEADERS)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # Iframe shell points at the raw endpoint.
    assert "/graph/raw" in body
    assert "<iframe" in body


# --- /graph/raw ------------------------------------------------------------

def test_graph_raw_returns_html_content_type(tmp_path):
    app_ref, _ = _app_ref(tmp_path)
    client = _client(app_ref)
    r = client.get("/graph/raw", headers=HEADERS)
    assert r.status_code == 200
    assert "text/html" in r.headers.get("Content-Type", "")
    body = r.get_data(as_text=True)
    assert "__graph_data" in body


def test_graph_raw_cached_across_two_hits_with_same_mtime(tmp_path):
    """Two GETs with no db change → graph_obsidian.render() called exactly once."""
    app_ref, _ = _app_ref(tmp_path)
    client = _client(app_ref)

    with patch("src.dashboard.graph_obsidian.render",
               return_value="<html>FAKE</html>") as mocked:
        r1 = client.get("/graph/raw", headers=HEADERS)
        r2 = client.get("/graph/raw", headers=HEADERS)
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.get_data(as_text=True) == "<html>FAKE</html>"
        assert r2.get_data(as_text=True) == "<html>FAKE</html>"
        assert mocked.call_count == 1, \
            f"cache miss: render() ran {mocked.call_count}× for unchanged db"


def test_graph_raw_refresh_param_busts_cache(tmp_path):
    """?refresh=1 must force a re-render even if mtime is unchanged."""
    app_ref, _ = _app_ref(tmp_path)
    client = _client(app_ref)

    with patch("src.dashboard.graph_obsidian.render",
               return_value="<html>FAKE</html>") as mocked:
        client.get("/graph/raw", headers=HEADERS)
        client.get("/graph/raw?refresh=1", headers=HEADERS)
        client.get("/graph/raw?refresh=1", headers=HEADERS)
        assert mocked.call_count == 3


def test_graph_raw_history_disabled_short_circuits(tmp_path):
    """If app_ref.history is None we must not blow up — return a benign HTML."""
    cfg = {
        "dashboard": {"enabled": True, "host": "127.0.0.1",
                      "port": 8766, "theme": "dark"},
        "history": {"db_path": str(tmp_path / "no.db")},
    }
    app_ref = types.SimpleNamespace(cfg=cfg, cfg_path="config.yaml", history=None)
    client = _client(app_ref)
    r = client.get("/graph/raw", headers=HEADERS)
    assert r.status_code == 200
    assert "History disabled" in r.get_data(as_text=True)
