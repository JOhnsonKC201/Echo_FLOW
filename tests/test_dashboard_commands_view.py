"""PR-E — Commands top-level page + recent-commands log integration."""
from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from src.history import History
from src.dashboard import commands_view


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


# --- page_data() helper ----------------------------------------------------

def test_page_data_off_by_default(tmp_path):
    cfg = {"experimental": {"command_mode": False, "command_prefix": "computer"}}
    h = History(str(tmp_path / "h.db"))
    data = commands_view.page_data(cfg, h)
    assert data["enabled"] is False
    assert data["prefix"] == "computer"
    assert isinstance(data["supported"], list) and len(data["supported"]) > 10
    assert data["recent"] == []


def test_page_data_picks_up_recent_log(tmp_path):
    cfg = {"experimental": {"command_mode": True, "command_prefix": "jarvis"}}
    h = History(str(tmp_path / "h.db"))
    h.log_command(body="save", action_type="hotkey", action_value="ctrl+s",
                  label="save", ok=True)
    h.log_command(body="garbage", action_type="unknown", action_value="",
                  label=None, ok=False)
    data = commands_view.page_data(cfg, h)
    assert data["enabled"] is True
    assert data["prefix"] == "jarvis"
    assert len(data["recent"]) == 2
    # Newest first → 'garbage' (unknown) is at index 0
    assert data["recent"][0]["action_type"] == "unknown"
    assert data["recent"][0]["ts_human"]  # formatted


def test_page_data_no_history_safe(tmp_path):
    cfg = {"experimental": {"command_mode": True}}
    data = commands_view.page_data(cfg, None)
    assert data["recent"] == []


# --- Route ------------------------------------------------------------------

def test_commands_page_renders(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/commands", headers=HOST)
    assert r.status_code == 200
    assert b"Commands" in r.data
    assert b"Supported commands" in r.data
    assert b"How to fire a command" in r.data


def test_commands_page_shows_disabled_banner_when_off(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/commands", headers=HOST)
    # config.yaml has command_mode: false out of the box.
    assert b"Command Mode is off" in r.data


def test_commands_page_renders_recent_log(tmp_path):
    client, app_ref = _client(tmp_path)
    app_ref.history.log_command(body="copy that", action_type="hotkey",
                                action_value="ctrl+c", label="copy", ok=True)
    r = client.get("/commands", headers=HOST)
    assert b"copy that" in r.data
    assert b"ctrl+c" in r.data


def test_commands_in_sidebar(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/commands", headers=HOST)
    assert b'href="/commands"' in r.data
