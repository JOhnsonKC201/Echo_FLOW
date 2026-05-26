"""Phase 5 acceptance tests — Style profiles CRUD + Cleaner provider."""
from __future__ import annotations

import pytest

from src.history import History
from src.dashboard import style_profiles as sp
from src.cleanup import Cleaner


def _h(tmp_path):
    return History(str(tmp_path / "h.db"))


def test_replace_all_persists_order(tmp_path):
    h = _h(tmp_path)
    sp.replace_all(h.conn, [
        {"style": "code", "matchers": ["Cursor", "Code"]},
        {"style": "email", "matchers": ["Gmail"]},
        {"style": "default", "matchers": []},
    ])
    out = sp.list_profiles(h.conn)
    assert [p["position"] for p in out] == [0, 1, 2]
    assert out[0]["style"] == "code"
    assert out[2]["matchers"] == []


def test_replace_all_rejects_unknown_style(tmp_path):
    h = _h(tmp_path)
    with pytest.raises(ValueError):
        sp.replace_all(h.conn, [{"style": "bogus", "matchers": []}])


def test_pick_style_first_match_wins(tmp_path):
    h = _h(tmp_path)
    sp.replace_all(h.conn, [
        {"style": "code", "matchers": ["Cursor"]},
        {"style": "email", "matchers": ["Gmail"]},
        {"style": "default", "matchers": []},
    ])
    assert sp.pick_style(h.conn, "Cursor – README.md") == "code"
    assert sp.pick_style(h.conn, "Gmail - inbox") == "email"
    assert sp.pick_style(h.conn, "Random Window") == "default"


def test_pick_style_falls_back_when_no_profiles(tmp_path):
    h = _h(tmp_path)
    assert sp.pick_style(h.conn, "Cursor", config_default="cfg-default") == "cfg-default"


def test_seed_from_config_idempotent(tmp_path):
    h = _h(tmp_path)
    cfg = [
        {"match": ["Code", "Cursor"], "style": "code"},
        {"match": [], "style": "default"},
    ]
    assert sp.seed_from_config(h.conn, cfg) == 2
    assert sp.seed_from_config(h.conn, cfg) == 0
    assert len(sp.list_profiles(h.conn)) == 2


# --- Cleaner style provider --------------------------------------------------

def test_cleaner_uses_style_provider():
    c = Cleaner({
        "enabled": True,
        "profiles": [{"match": ["Notepad"], "style": "casual"}],
    })
    c.set_style_provider(lambda title: "code" if "Cursor" in title else "")
    assert c.pick_style("Cursor – x.py") == "code"
    # Provider returning empty -> fall through to cfg.
    assert c.pick_style("Notepad - X") == "casual"


def test_cleaner_provider_error_falls_back():
    c = Cleaner({
        "enabled": True,
        "profiles": [{"match": [], "style": "default"}],
    })
    c.set_style_provider(lambda title: (_ for _ in ()).throw(RuntimeError("db")))
    assert c.pick_style("anything") == "default"


# --- Route round-trip --------------------------------------------------------

class _App:
    def __init__(self, history):
        self.cfg = {
            "dashboard": {"host": "127.0.0.1", "port": 8766},
            "cleanup": {"profiles": [
                {"match": ["Code"], "style": "code"},
                {"match": [], "style": "default"},
            ]},
        }
        self.history = history
        self.reload_calls = 0
    def reload_config(self):
        self.reload_calls += 1


def test_style_route_seeds_from_config_on_first_open(tmp_path):
    from src.dashboard.app import make_app
    h = _h(tmp_path)
    app_ref = _App(h)
    client = make_app(app_ref).test_client()
    r = client.get("/style", headers={"Host": "127.0.0.1:8766"})
    assert r.status_code == 200
    assert b"code" in r.data
    # Subsequent open doesn't re-seed.
    assert len(sp.list_profiles(h.conn)) == 2


def test_style_route_save_round_trip(tmp_path):
    from src.dashboard.app import make_app
    h = _h(tmp_path)
    app_ref = _App(h)
    client = make_app(app_ref).test_client()
    # Provide 3 rows, one with no matchers. Use raw URL-encoded body so
    # repeated field names are preserved (Flask's getlist relies on this).
    body = (
        "style=code&matchers=Cursor%2C%20PyCharm"
        "&style=email&matchers=Gmail%0AOutlook"
        "&style=default&matchers="
    )
    r = client.post(
        "/style/save",
        headers={"Host": "127.0.0.1:8766",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data=body,
    )
    assert r.status_code == 302
    profiles = sp.list_profiles(h.conn)
    assert len(profiles) == 3
    assert profiles[0]["matchers"] == ["Cursor", "PyCharm"]
    assert profiles[1]["matchers"] == ["Gmail", "Outlook"]
    assert profiles[2]["matchers"] == []
    assert app_ref.reload_calls == 1
