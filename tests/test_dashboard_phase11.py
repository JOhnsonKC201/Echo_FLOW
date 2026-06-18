"""Phase 11 — Window state, theme toggle, onboarding tour."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from src.history import History


REPO_CFG = Path(__file__).resolve().parent.parent / "config.yaml"


class _App:
    def __init__(self, cfg, cfg_path, history):
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.history = history


HOST = {"Host": "127.0.0.1:8766"}


def _client(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    shutil.copy(REPO_CFG, cfg_path)
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    h = History(str(tmp_path / "h.db"))
    app_ref = _App(cfg, cfg_path, h)
    from src.dashboard.app import make_app
    return make_app(app_ref).test_client(), app_ref


# --- Window state persistence ----------------------------------------------

def test_load_window_state_missing_returns_empty(monkeypatch, tmp_path):
    from src.dashboard import window as w
    monkeypatch.setattr(w, "_STATE_FILE", tmp_path / "nope.json")
    assert w._load_window_state() == {}


def test_save_then_load_window_state(monkeypatch, tmp_path):
    from src.dashboard import window as w
    monkeypatch.setattr(w, "_STATE_FILE", tmp_path / "win.json")
    # Pin a large screen: the real screen may be <= the saved size (a headless
    # CI session reports 1024x768), which would trip the maximized-size guard
    # in _save_window_state and discard the dimensions we're round-tripping.
    monkeypatch.setattr(w, "_primary_screen_size", lambda: (3840, 2160))
    class _Win:
        width = 1024; height = 768
        def get_size(self):
            return (1024, 768)
    w._save_window_state(_Win())
    state = w._load_window_state()
    # x/y intentionally NOT persisted — off-screen-restore guard.
    assert state == {"width": 1024, "height": 768}


def test_save_window_state_falls_back_to_attrs_when_get_size_missing(monkeypatch, tmp_path):
    from src.dashboard import window as w
    monkeypatch.setattr(w, "_STATE_FILE", tmp_path / "win.json")
    class _Win:
        width = 999; height = 555
    w._save_window_state(_Win())
    assert w._load_window_state() == {"width": 999, "height": 555}


def test_load_window_state_malformed_falls_back(monkeypatch, tmp_path):
    from src.dashboard import window as w
    path = tmp_path / "win.json"
    path.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(w, "_STATE_FILE", path)
    assert w._load_window_state() == {}


# --- Theme toggle ----------------------------------------------------------

def test_theme_toggle_flips_default_to_opposite(tmp_path):
    client, app_ref = _client(tmp_path)
    # Default is whatever config.yaml ships with; toggling flips it.
    start = (app_ref.cfg.get("dashboard") or {}).get("theme", "dark")
    opposite = "dark" if start == "light" else "light"
    r = client.post("/api/theme", headers=HOST)
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data["ok"] is True and data["theme"] == opposite
    reparsed = yaml.safe_load(app_ref.cfg_path.read_text(encoding="utf-8"))
    assert reparsed["dashboard"]["theme"] == opposite


def test_theme_toggle_explicit_value(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/api/theme", headers=HOST, data={"theme": "light"})
    assert r.status_code == 200
    assert json.loads(r.data)["theme"] == "light"
    r2 = client.post("/api/theme", headers=HOST, data={"theme": "dark"})
    assert json.loads(r2.data)["theme"] == "dark"


def test_theme_toggle_rejects_garbage_and_flips_instead(tmp_path):
    client, app_ref = _client(tmp_path)
    # An invalid value falls back to the toggle behavior.
    r = client.post("/api/theme", headers=HOST, data={"theme": "rainbow"})
    assert r.status_code == 200
    assert json.loads(r.data)["theme"] in ("dark", "light")


def test_theme_button_rendered_in_base(tmp_path):
    client, _ = _client(tmp_path)
    # Have to finish onboarding first to get past the redirect.
    client.post("/onboarding/finish", headers=HOST)
    r = client.get("/", headers=HOST)
    assert b"theme-toggle" in r.data


# --- Onboarding ------------------------------------------------------------

def test_first_run_redirects_to_onboarding(tmp_path):
    # Explicitly set first-run state — don't depend on the on-disk config.yaml's
    # current value of dashboard.onboarded.
    client, app_ref = _client(tmp_path)
    app_ref.cfg["dashboard"]["onboarded"] = False
    r = client.get("/", headers=HOST, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/onboarding")


def test_onboarding_page_renders(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/onboarding", headers=HOST)
    assert r.status_code == 200
    assert b"Welcome to Echo Flow" in r.data
    assert b"Push-to-talk" in r.data


def test_onboarding_finish_persists_and_unblocks_home(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/onboarding/finish", headers=HOST)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/")
    reparsed = yaml.safe_load(app_ref.cfg_path.read_text(encoding="utf-8"))
    assert reparsed["dashboard"]["onboarded"] is True
    # Subsequent / should render Home directly, not redirect.
    r2 = client.get("/", headers=HOST, follow_redirects=False)
    assert r2.status_code == 200
