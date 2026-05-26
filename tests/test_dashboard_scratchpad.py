"""Phase 7 acceptance tests — Scratchpad CRUD + dictate-into routing."""
from __future__ import annotations

import types
import pytest

from src.history import History
from src.dashboard import scratchpad as sp


def _h(tmp_path):
    return History(str(tmp_path / "h.db"))


# --- CRUD --------------------------------------------------------------------

def test_create_with_auto_title(tmp_path):
    h = _h(tmp_path)
    pid = sp.create_scratchpad(h.conn, body="My first sentence. More text after.")
    pad = sp.get_scratchpad(h.conn, pid)
    assert pad["title"] == "My first sentence"
    assert pad["body"].startswith("My first sentence")


def test_create_empty_uses_untitled(tmp_path):
    h = _h(tmp_path)
    pid = sp.create_scratchpad(h.conn)
    pad = sp.get_scratchpad(h.conn, pid)
    assert pad["title"] == "Untitled"


def test_create_with_explicit_title(tmp_path):
    h = _h(tmp_path)
    pid = sp.create_scratchpad(h.conn, title="Meeting notes", body="hello")
    assert sp.get_scratchpad(h.conn, pid)["title"] == "Meeting notes"


def test_save_round_trip(tmp_path):
    h = _h(tmp_path)
    pid = sp.create_scratchpad(h.conn, title="A", body="b")
    assert sp.save_scratchpad(h.conn, pid, title="A2", body="b2") is True
    pad = sp.get_scratchpad(h.conn, pid)
    assert pad["title"] == "A2" and pad["body"] == "b2"


def test_save_blank_title_auto_titles_from_body(tmp_path):
    h = _h(tmp_path)
    pid = sp.create_scratchpad(h.conn, title="orig", body="x")
    sp.save_scratchpad(h.conn, pid, title="   ", body="New auto title here. trailing")
    assert sp.get_scratchpad(h.conn, pid)["title"] == "New auto title here"


def test_append_bumps_updated_at(tmp_path):
    h = _h(tmp_path)
    pid = sp.create_scratchpad(h.conn, body="one")
    before = sp.get_scratchpad(h.conn, pid)["updated_at"]
    import time as _t
    _t.sleep(0.01)
    sp.append_to_scratchpad(h.conn, pid, "two")
    pad = sp.get_scratchpad(h.conn, pid)
    assert pad["body"] == "one\ntwo"
    assert pad["updated_at"] > before


def test_delete(tmp_path):
    h = _h(tmp_path)
    pid = sp.create_scratchpad(h.conn, body="x")
    assert sp.delete_scratchpad(h.conn, pid) is True
    assert sp.get_scratchpad(h.conn, pid) is None
    assert sp.delete_scratchpad(h.conn, 9999) is False


def test_list_sorted_by_updated_desc(tmp_path):
    h = _h(tmp_path)
    a = sp.create_scratchpad(h.conn, title="A")
    b = sp.create_scratchpad(h.conn, title="B")
    import time as _t
    _t.sleep(0.01)
    sp.append_to_scratchpad(h.conn, a, "more")
    items = sp.list_scratchpads(h.conn)
    assert items[0]["id"] == a  # most recent update


# --- Route round-trip --------------------------------------------------------

class _App:
    def __init__(self, history):
        self.cfg = {"dashboard": {"host": "127.0.0.1", "port": 8766}}
        self.history = history
        self._scratchpad_target_id = None


def _client(tmp_path):
    from src.dashboard.app import make_app
    h = _h(tmp_path)
    app_ref = _App(h)
    return make_app(app_ref).test_client(), app_ref


def test_scratchpad_list_empty(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/scratchpad", headers={"Host": "127.0.0.1:8766"})
    assert r.status_code == 200
    assert b"No scratchpads yet" in r.data


def test_scratchpad_new_redirects_to_edit(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/scratchpad/new", headers={"Host": "127.0.0.1:8766"},
                    data={"title": "Test pad"})
    assert r.status_code == 302
    assert r.headers["Location"].startswith("/scratchpad/")


def test_scratchpad_edit_save(tmp_path):
    client, app_ref = _client(tmp_path)
    pid = sp.create_scratchpad(app_ref.history.conn, title="orig", body="body")
    r = client.post("/scratchpad/save", headers={"Host": "127.0.0.1:8766"},
                    data={"id": str(pid), "title": "renamed", "body": "new body"})
    assert r.status_code == 302
    pad = sp.get_scratchpad(app_ref.history.conn, pid)
    assert pad["title"] == "renamed" and pad["body"] == "new body"


def test_scratchpad_delete_clears_target(tmp_path):
    client, app_ref = _client(tmp_path)
    pid = sp.create_scratchpad(app_ref.history.conn, title="x")
    app_ref._scratchpad_target_id = pid
    client.post("/scratchpad/delete", headers={"Host": "127.0.0.1:8766"},
                data={"id": str(pid)})
    assert app_ref._scratchpad_target_id is None


def test_scratchpad_target_toggles(tmp_path):
    client, app_ref = _client(tmp_path)
    pid = sp.create_scratchpad(app_ref.history.conn, title="x")
    client.post("/scratchpad/target", headers={"Host": "127.0.0.1:8766"},
                data={"id": str(pid)})
    assert app_ref._scratchpad_target_id == pid
    # Posting again with same id clears.
    client.post("/scratchpad/target", headers={"Host": "127.0.0.1:8766"},
                data={"id": str(pid)})
    assert app_ref._scratchpad_target_id is None


def test_edit_view_404_for_missing(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/scratchpad/999", headers={"Host": "127.0.0.1:8766"})
    assert r.status_code == 404
