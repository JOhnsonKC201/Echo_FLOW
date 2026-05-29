"""Phase 14 PR 3 — Actions top-level page + voice_actions log integration."""
from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from src.history import History
from src.dashboard import actions_view


REPO_CFG = Path(__file__).resolve().parent.parent / "config.yaml"
HOST = {"Host": "127.0.0.1:8766"}


class _App:
    def __init__(self, cfg, cfg_path, history):
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.history = history


def _client(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    shutil.copy(REPO_CFG, cfg_path)
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg["dashboard"]["onboarded"] = True
    h = History(str(tmp_path / "h.db"))
    app_ref = _App(cfg, cfg_path, h)
    from src.dashboard.app import make_app
    return make_app(app_ref).test_client(), app_ref


# --- page_data() helper -----------------------------------------------------

def test_page_data_off_by_default(tmp_path):
    cfg = {"experimental": {"action_mode": False, "command_prefix": "computer",
                            "action_apps": {"spotify": "spotify"}}}
    h = History(str(tmp_path / "h.db"))
    data = actions_view.page_data(cfg, h)
    assert data["enabled"] is False
    assert data["prefix"] == "computer"
    assert data["apps"] == [{"name": "spotify", "target": "spotify"}]
    assert isinstance(data["supported"], list) and len(data["supported"]) >= 5
    assert data["recent"] == []


def test_page_data_picks_up_recent_actions(tmp_path):
    cfg = {"experimental": {"action_mode": True, "command_prefix": "jarvis"}}
    h = History(str(tmp_path / "h.db"))
    h.log_action(body="open notepad", handler="open_app",
                 args_json='{"app": "notepad"}', label="Open notepad", ok=True)
    h.log_action(body="open frobnicate", handler="open_app",
                 args_json='{"app": "frobnicate"}', label="Open frobnicate",
                 ok=False, error="I don't have an app called frobnicate configured.")
    data = actions_view.page_data(cfg, h)
    assert data["enabled"] is True
    assert data["prefix"] == "jarvis"
    assert len(data["recent"]) == 2
    assert data["recent"][0]["ok"] is False     # newest first
    assert data["recent"][0]["ts_human"]


def test_page_data_no_history_safe(tmp_path):
    data = actions_view.page_data({"experimental": {"action_mode": True}}, None)
    assert data["recent"] == []


# --- Route -------------------------------------------------------------------

def test_actions_page_renders(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/actions", headers=HOST)
    assert r.status_code == 200
    assert b"Supported actions" in r.data
    assert b"How to fire an action" in r.data


def test_actions_page_shows_disabled_banner_when_off(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/actions", headers=HOST)
    assert b"Action Mode is off" in r.data   # config.yaml ships action_mode: false


def test_actions_page_renders_recent_log(tmp_path):
    client, app_ref = _client(tmp_path)
    app_ref.history.log_action(body="search the web for cats", handler="web_search",
                               args_json='{"query": "<redacted len=4>"}',
                               label="Search", ok=True)
    r = client.get("/actions", headers=HOST)
    assert b"search the web for cats" in r.data
    assert b"web_search" in r.data


def test_actions_in_sidebar(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/actions", headers=HOST)
    assert b'href="/actions"' in r.data


# --- Settings toggle ---------------------------------------------------------

def test_experimental_settings_shows_action_toggle(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/settings/experimental", headers=HOST)
    assert r.status_code == 200
    assert b'name="action_mode"' in r.data
    assert b'name="action_email_url"' in r.data


def test_experimental_save_persists_action_mode(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/settings/experimental/save", headers=HOST, data={
        "command_prefix": "computer",
        "action_mode": "1",
        "action_email_url": "https://mail.google.com",
    })
    assert r.status_code in (302, 303)
    cfg = yaml.safe_load(Path(app_ref.cfg_path).read_text(encoding="utf-8"))
    assert cfg["experimental"]["action_mode"] is True


def test_experimental_save_rejects_unsafe_email_url(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/settings/experimental/save", headers=HOST, data={
        "command_prefix": "computer",
        "action_mode": "1",
        "action_email_url": "javascript:alert(1)",
    }, follow_redirects=False)
    # Redirected back with a flash; the bad URL is not persisted.
    cfg = yaml.safe_load(Path(app_ref.cfg_path).read_text(encoding="utf-8"))
    assert cfg["experimental"]["action_email_url"] != "javascript:alert(1)"
