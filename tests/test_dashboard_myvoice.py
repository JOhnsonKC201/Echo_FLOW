"""/myvoice dashboard page — sample CRUD + shadow review rendering."""
from __future__ import annotations

import pytest

from src.history import History
from src.dashboard import voice_samples as vs
from src import voice_profile as vp


HDR = {"Host": "127.0.0.1:8766"}


@pytest.fixture(autouse=True)
def _clear_profile_cache():
    # voice_profile caches the profile in module-level state; clear it around
    # each test so a sample added directly (not via the route) is seen.
    vp.invalidate()
    yield
    vp.invalidate()


def _h(tmp_path):
    return History(str(tmp_path / "h.db"))


class _App:
    def __init__(self, history, humanize="shadow"):
        self.cfg = {"dashboard": {"host": "127.0.0.1", "port": 8766},
                    "experimental": {"humanize": humanize}}
        self.history = history
        self.retriever = None


def _client(tmp_path, humanize="shadow"):
    from src.dashboard.app import make_app
    h = _h(tmp_path)
    app_ref = _App(h, humanize)
    return make_app(app_ref).test_client(), app_ref, h


def test_myvoice_get_empty(tmp_path):
    client, _, _ = _client(tmp_path)
    r = client.get("/myvoice", headers=HDR)
    assert r.status_code == 200
    assert b"No samples yet" in r.data
    assert b"My Voice" in r.data


def test_myvoice_add_and_delete(tmp_path):
    client, _, h = _client(tmp_path)
    client.post("/myvoice/add", headers=HDR,
                data={"content": "This is exactly how I write things."})
    r = client.get("/myvoice", headers=HDR)
    assert b"exactly how I write" in r.data
    sid = vs.list_samples(h.conn)[0]["id"]
    client.post("/myvoice/delete", headers=HDR, data={"id": str(sid)})
    assert vs.list_samples(h.conn) == []


def test_myvoice_toggle(tmp_path):
    client, _, h = _client(tmp_path)
    sid = vs.add_sample(h.conn, "toggle me")
    client.post("/myvoice/toggle", headers=HDR, data={"id": str(sid), "enabled": "0"})
    assert vs.enabled_texts(h.conn) == []
    client.post("/myvoice/toggle", headers=HDR, data={"id": str(sid), "enabled": "1"})
    assert vs.enabled_texts(h.conn) == ["toggle me"]


def test_myvoice_import(tmp_path):
    client, _, h = _client(tmp_path)
    client.post("/myvoice/import", headers=HDR,
                data={"bulk": "block one\n\nblock two"})
    assert len(vs.list_samples(h.conn)) == 2


def test_myvoice_renders_shadow_rows(tmp_path):
    client, _, h = _client(tmp_path)
    h.log_humanize_shadow(cleaned_text="the cleaned sentence",
                          humanized_text="the sentence, my way",
                          style="polished", similarity=0.92)
    r = client.get("/myvoice", headers=HDR)
    assert b"Shadow preview" in r.data
    assert b"the sentence, my way" in r.data
    assert b"0.92" in r.data


def test_myvoice_in_sidebar(tmp_path):
    client, _, _ = _client(tmp_path)
    r = client.get("/myvoice", headers=HDR)
    assert b'href="/myvoice"' in r.data


def test_myvoice_renders_profile_preview(tmp_path):
    client, _, h = _client(tmp_path)
    vs.add_sample(h.conn, "a distinctive phrase I always use")
    r = client.get("/myvoice", headers=HDR)
    assert b"What Echo Flow learned" in r.data
    assert b"a distinctive phrase I always use" in r.data
