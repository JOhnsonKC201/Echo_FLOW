"""Branding (logo everywhere) + expanded notification-sound catalog."""
from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from src.history import History
from src import sound


REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_CFG = REPO_ROOT / "config.yaml"
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
    from src.dashboard.app import make_app
    return make_app(_App(cfg, cfg_path, h)).test_client()


# --- Sound catalog ----------------------------------------------------------

def test_list_choices_is_rich_and_well_formed():
    choices = sound.list_choices()
    assert len(choices) >= 20            # "more notification sounds"
    for c in choices:
        assert set(c) == {"value", "label", "available"}
        assert c["value"] and c["label"]
    # System aliases are always available.
    aliases = [c for c in choices if c["value"].startswith("System")]
    assert aliases and all(c["available"] for c in aliases)


def test_choices_cover_aliases_and_wavs():
    values = {c["value"] for c in sound.list_choices()}
    assert "SystemNotification" in values
    assert "Windows Notify.wav" in values
    assert "tada.wav" in values


def test_system_settings_renders_expanded_picker(tmp_path):
    r = _client(tmp_path).get("/settings/system", headers=HOST)
    assert r.status_code == 200
    # Several catalog entries are present in the rendered datalist.
    for token in (b"Windows Notify.wav", b"SystemNotification", b"tada.wav"):
        assert token in r.data


# --- Logo everywhere --------------------------------------------------------

def test_favicon_link_present(tmp_path):
    r = _client(tmp_path).get("/settings/system", headers=HOST)
    assert b'rel="icon"' in r.data
    assert b"logo.png" in r.data


def test_sidebar_uses_logo_image(tmp_path):
    r = _client(tmp_path).get("/settings/system", headers=HOST)
    assert b"logo-img" in r.data


def test_static_logo_is_served(tmp_path):
    r = _client(tmp_path).get("/static/logo.png", headers=HOST)
    assert r.status_code == 200
    assert r.mimetype == "image/png"


def test_asset_icons_exist():
    assert (REPO_ROOT / "assets" / "icon.png").is_file()
    assert (REPO_ROOT / "assets" / "icon.ico").is_file()
    assert (REPO_ROOT / "src" / "dashboard" / "static" / "logo.png").is_file()
