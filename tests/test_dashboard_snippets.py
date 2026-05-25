"""Phase 4 acceptance tests — Snippets CRUD + cleanup provider wiring."""
from __future__ import annotations

import pytest

from src.history import History
from src.dashboard import snippets as sn
from src.cleanup import Cleaner


def _h(tmp_path):
    return History(str(tmp_path / "h.db"))


# --- CRUD --------------------------------------------------------------------

def test_add_and_list(tmp_path):
    h = _h(tmp_path)
    sn.add_snippet(h.conn, "btw", "by the way")
    sn.add_snippet(h.conn, "fyi", "for your information")
    rows = sn.list_snippets(h.conn)
    assert len(rows) == 2
    codes = [r["code"] for r in rows]
    assert codes == sorted(codes, key=str.lower)


def test_add_is_upsert(tmp_path):
    h = _h(tmp_path)
    sid = sn.add_snippet(h.conn, "btw", "by the way")
    sid2 = sn.add_snippet(h.conn, "btw", "By The Way")
    assert sid == sid2
    rows = sn.list_snippets(h.conn)
    assert len(rows) == 1
    assert rows[0]["expansion"] == "By The Way"


def test_add_validates(tmp_path):
    h = _h(tmp_path)
    with pytest.raises(ValueError):
        sn.add_snippet(h.conn, "", "x")
    with pytest.raises(ValueError):
        sn.add_snippet(h.conn, "x", "")
    with pytest.raises(ValueError):
        sn.add_snippet(h.conn, "x" * 41, "x")


def test_delete(tmp_path):
    h = _h(tmp_path)
    sid = sn.add_snippet(h.conn, "tbh", "to be honest")
    assert sn.delete_snippet(h.conn, sid) is True
    assert sn.list_snippets(h.conn) == []


def test_bulk_import_with_arrow_or_equals(tmp_path):
    h = _h(tmp_path)
    raw = """
    btw = by the way
    fyi -> for your information
    # comment line ignored

    bad_line_no_separator
    """
    r = sn.bulk_import(h.conn, raw)
    assert r["added"] == 2
    assert r["invalid"] == 1  # bad_line_no_separator
    codes = [s["code"] for s in sn.list_snippets(h.conn)]
    assert set(codes) == {"btw", "fyi"}


def test_bulk_import_update_counts_existing(tmp_path):
    h = _h(tmp_path)
    sn.add_snippet(h.conn, "btw", "old")
    r = sn.bulk_import(h.conn, "btw = by the way")
    assert r["updated"] == 1
    assert r["added"] == 0


# --- merged_snippet_map fallback semantics -----------------------------------

def test_merged_returns_config_when_table_empty(tmp_path):
    h = _h(tmp_path)
    defaults = {"btw": "by the way"}
    assert sn.merged_snippet_map(h.conn, defaults) == {"btw": "by the way"}


def test_merged_returns_table_when_present(tmp_path):
    h = _h(tmp_path)
    sn.add_snippet(h.conn, "lgtm", "looks good to me")
    defaults = {"btw": "by the way"}
    merged = sn.merged_snippet_map(h.conn, defaults)
    assert merged == {"lgtm": "looks good to me"}  # UI is authoritative


def test_seed_from_config_idempotent(tmp_path):
    h = _h(tmp_path)
    defaults = {"btw": "by the way", "fyi": "for your information"}
    assert sn.seed_from_config(h.conn, defaults) == 2
    # Second call must not double-insert.
    assert sn.seed_from_config(h.conn, defaults) == 0
    assert len(sn.list_snippets(h.conn)) == 2


# --- Cleaner snippet provider -------------------------------------------------

def test_cleaner_uses_provider_when_set():
    c = Cleaner({"enabled": True, "provider": "ollama"})
    c.set_snippets_provider(lambda: {"btw": "by the way"})
    out = c._expand_snippets("ok btw later")
    assert out == "ok by the way later"


def test_cleaner_falls_back_to_cfg_when_no_provider():
    c = Cleaner({
        "enabled": True, "provider": "ollama",
        "snippets": {"fyi": "for your information"},
    })
    out = c._expand_snippets("fyi tomorrow")
    assert out == "for your information tomorrow"


def test_cleaner_provider_error_falls_back_to_cfg():
    def _boom():
        raise RuntimeError("db down")
    c = Cleaner({
        "enabled": True, "provider": "ollama",
        "snippets": {"btw": "by the way"},
    })
    c.set_snippets_provider(_boom)
    out = c._expand_snippets("btw")
    assert out == "by the way"


# --- Snippets route end-to-end ------------------------------------------------

class _App:
    def __init__(self, history):
        self.cfg = {"dashboard": {"host": "127.0.0.1", "port": 8766}}
        self.history = history
        self.reload_calls = 0
    def reload_config(self):
        self.reload_calls += 1


def _client(tmp_path):
    from src.dashboard.app import make_app
    h = _h(tmp_path)
    app_ref = _App(h)
    return make_app(app_ref).test_client(), app_ref


def test_snippets_route_get_empty(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/snippets", headers={"Host": "127.0.0.1:8766"})
    assert r.status_code == 200
    assert b"No snippets yet" in r.data


def test_snippets_add_round_trip(tmp_path):
    client, app_ref = _client(tmp_path)
    client.post("/snippets/add", headers={"Host": "127.0.0.1:8766"},
                data={"code": "btw", "expansion": "by the way"})
    r = client.get("/snippets", headers={"Host": "127.0.0.1:8766"})
    assert b"btw" in r.data
    assert b"by the way" in r.data
    assert app_ref.reload_calls == 1
