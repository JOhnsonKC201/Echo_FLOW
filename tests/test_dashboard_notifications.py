"""Phase 9 — Notifications inbox: persistence, list, mark-read, badge JSON."""
from __future__ import annotations

import json

from src.history import History
from src.dashboard import notifications as nf


def _h(tmp_path):
    return History(str(tmp_path / "h.db"))


# --- Data layer --------------------------------------------------------------

def test_insert_then_list_returns_newest_first(tmp_path):
    h = _h(tmp_path)
    a = nf.insert(h.conn, "info", "first", "alpha")
    b = nf.insert(h.conn, "warning", "second", "beta")
    items = nf.list_recent(h.conn)
    assert [i["id"] for i in items] == [b, a]
    assert items[0]["title"] == "second" and items[0]["level"] == "warning"


def test_insert_normalizes_unknown_level(tmp_path):
    h = _h(tmp_path)
    nid = nf.insert(h.conn, "BOGUS", "x", "y")
    items = nf.list_recent(h.conn)
    assert items[0]["level"] == "info"
    assert items[0]["id"] == nid


def test_unread_count_and_mark(tmp_path):
    h = _h(tmp_path)
    a = nf.insert(h.conn, "info", "a", "1")
    b = nf.insert(h.conn, "info", "b", "2")
    c = nf.insert(h.conn, "info", "c", "3")
    assert nf.unread_count(h.conn) == 3
    assert nf.mark_read(h.conn, b) is True
    assert nf.unread_count(h.conn) == 2
    # Marking same id again is a no-op.
    assert nf.mark_read(h.conn, b) is False
    assert nf.mark_all_read(h.conn) == 2
    assert nf.unread_count(h.conn) == 0


def test_mark_read_invalid_id(tmp_path):
    h = _h(tmp_path)
    assert nf.mark_read(h.conn, 0) is False
    assert nf.mark_read(h.conn, -3) is False


# --- notify.py sink hook -----------------------------------------------------

def test_notify_sink_persists_through_notify_module(tmp_path, monkeypatch):
    h = _h(tmp_path)
    from src import notify as wn
    # Reset rate-limit + sink state across tests.
    wn._last_msg = ("", 0.0)
    wn.set_sink(lambda lvl, t, b: nf.insert(h.conn, lvl, t, b))
    # Avoid the threaded toast path side effects.
    monkeypatch.setattr(wn, "_winsdk_toast", lambda *a, **k: True)
    wn.notify("hello", "world", "warning")
    items = nf.list_recent(h.conn)
    assert items and items[0]["title"] == "hello" and items[0]["level"] == "warning"
    # Clean up so other tests don't inherit our sink.
    wn.set_sink(None)


# --- Routes ------------------------------------------------------------------

class _App:
    def __init__(self, history):
        self.cfg = {"dashboard": {"host": "127.0.0.1", "port": 8766}}
        self.history = history


HOST = {"Host": "127.0.0.1:8766"}


def _client(tmp_path):
    h = _h(tmp_path)
    app_ref = _App(h)
    from src.dashboard.app import make_app
    return make_app(app_ref).test_client(), app_ref


def test_notifications_page_empty(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/notifications", headers=HOST)
    assert r.status_code == 200
    assert b"No notifications yet" in r.data


def test_notifications_page_lists_items(tmp_path):
    client, app_ref = _client(tmp_path)
    nf.insert(app_ref.history.conn, "info", "hello", "world")
    r = client.get("/notifications", headers=HOST)
    assert r.status_code == 200
    assert b"hello" in r.data and b"world" in r.data


def test_unread_json_endpoint(tmp_path):
    client, app_ref = _client(tmp_path)
    nf.insert(app_ref.history.conn, "info", "a", "b")
    nf.insert(app_ref.history.conn, "info", "c", "d")
    r = client.get("/api/notifications/unread.json", headers=HOST)
    assert r.status_code == 200
    assert json.loads(r.data)["unread"] == 2


def test_mark_read_route(tmp_path):
    client, app_ref = _client(tmp_path)
    nid = nf.insert(app_ref.history.conn, "info", "a", "b")
    r = client.post("/notifications/mark-read", headers=HOST, data={"id": str(nid)})
    assert r.status_code == 302
    assert nf.unread_count(app_ref.history.conn) == 0


def test_mark_all_read_route(tmp_path):
    client, app_ref = _client(tmp_path)
    nf.insert(app_ref.history.conn, "info", "a", "b")
    nf.insert(app_ref.history.conn, "info", "c", "d")
    r = client.post("/notifications/mark-all-read", headers=HOST)
    assert r.status_code == 302
    assert nf.unread_count(app_ref.history.conn) == 0
