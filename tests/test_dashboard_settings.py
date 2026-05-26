"""Phase 8 acceptance tests — Settings panels (config_writer round-trip)."""
from __future__ import annotations

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
        self._reload_calls = 0

    def reload_config(self):
        self._reload_calls += 1


def _client(tmp_path):
    """Spin up a Flask test client backed by a copy of the real config.yaml."""
    cfg_path = tmp_path / "config.yaml"
    shutil.copy(REPO_CFG, cfg_path)
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    h = History(str(tmp_path / "h.db"))
    app_ref = _App(cfg, cfg_path, h)
    from src.dashboard.app import make_app
    return make_app(app_ref).test_client(), app_ref


HOST = {"Host": "127.0.0.1:8766"}


# --- GETs render -------------------------------------------------------------

@pytest.mark.parametrize("path,marker", [
    ("/settings/general", b"Push-to-talk hotkey"),
    ("/settings/system", b"Voice-activity detection"),
    ("/settings/vibe", b"Auto Cleanup"),
    ("/settings/experimental", b"Press-Enter command"),
    ("/settings/privacy", b"Wipe history"),
])
def test_settings_pages_render(tmp_path, path, marker):
    client, _ = _client(tmp_path)
    r = client.get(path, headers=HOST)
    assert r.status_code == 200
    assert marker in r.data


def test_settings_tabs_present_on_each_page(tmp_path):
    client, _ = _client(tmp_path)
    for path in ("/settings/general", "/settings/system", "/settings/vibe"):
        r = client.get(path, headers=HOST)
        assert b"settings-tab" in r.data


# --- General save round-trip ------------------------------------------------

def test_general_save_persists_to_yaml(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/settings/general/save", headers=HOST, data={
        "hotkey_combo": "ctrl+alt+w",
        "hotkey_mode": "toggle",
        "paste_last_combo": "",
        "whisper_language": "es",
    })
    assert r.status_code == 302
    reparsed = yaml.safe_load(app_ref.cfg_path.read_text(encoding="utf-8"))
    assert reparsed["hotkey"]["combo"] == "ctrl+alt+w"
    assert reparsed["hotkey"]["mode"] == "toggle"
    assert reparsed["hotkey"]["paste_last_combo"] in ("", None)
    assert reparsed["whisper"]["language"] == "es"


def test_general_save_rejects_invalid_mode(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/settings/general/save", headers=HOST, data={
        "hotkey_combo": "ctrl+shift",
        "hotkey_mode": "spin",
        "paste_last_combo": "",
        "whisper_language": "en",
    })
    assert r.status_code == 302
    assert "mode%20must%20be" in r.headers["Location"]


def test_general_save_rejects_empty_combo(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/settings/general/save", headers=HOST, data={
        "hotkey_combo": "  ",
        "hotkey_mode": "hold",
        "paste_last_combo": "",
        "whisper_language": "en",
    })
    assert r.status_code == 302
    assert "cannot%20be%20empty" in r.headers["Location"]


def test_general_restart_banner_present(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/settings/general", headers=HOST)
    assert b"restart-banner" in r.data


# --- System save -------------------------------------------------------------

def test_system_save_toggles_and_reload(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/settings/system/save", headers=HOST, data={
        # sound_enabled omitted → off
        "vad_enabled": "1",
        "silence_timeout_ms": "2500",
    })
    assert r.status_code == 302
    reparsed = yaml.safe_load(app_ref.cfg_path.read_text(encoding="utf-8"))
    assert reparsed["sound"]["enabled"] is False
    assert reparsed["audio"]["vad_enabled"] is True
    assert reparsed["audio"]["silence_timeout_ms"] == 2500
    assert app_ref._reload_calls == 1


def test_system_save_rejects_out_of_range_timeout(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/settings/system/save", headers=HOST, data={
        "vad_enabled": "1",
        "silence_timeout_ms": "50",
    })
    assert r.status_code == 302
    assert "200..10000" in r.headers["Location"] or "200..10000" in r.headers["Location"].replace("%2E", ".")


# --- Vibe save ---------------------------------------------------------------

def test_vibe_save_toggles_all_checkboxes_off(tmp_path):
    client, app_ref = _client(tmp_path)
    # POST with no checkboxes ticked → all become false.
    r = client.post("/settings/vibe/save", headers=HOST, data={})
    assert r.status_code == 302
    reparsed = yaml.safe_load(app_ref.cfg_path.read_text(encoding="utf-8"))
    assert reparsed["cleanup"]["enabled"] is False
    assert reparsed["cleanup"]["skip_when_clean"] is False
    assert reparsed["cleanup"]["learning"]["enabled"] is False
    assert reparsed["prompt_engineering"]["enabled"] is False
    assert app_ref._reload_calls == 1


def test_vibe_save_partial_set(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/settings/vibe/save", headers=HOST, data={
        "cleanup_enabled": "1",
        "skip_when_clean": "1",
        # learning_enabled, prompt_engineering_enabled omitted
    })
    assert r.status_code == 302
    reparsed = yaml.safe_load(app_ref.cfg_path.read_text(encoding="utf-8"))
    assert reparsed["cleanup"]["enabled"] is True
    assert reparsed["cleanup"]["skip_when_clean"] is True
    assert reparsed["cleanup"]["learning"]["enabled"] is False
    assert reparsed["prompt_engineering"]["enabled"] is False


# --- Experimental save -------------------------------------------------------

def test_experimental_save(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/settings/experimental/save", headers=HOST, data={
        "press_enter_command": "1",
    })
    assert r.status_code == 302
    reparsed = yaml.safe_load(app_ref.cfg_path.read_text(encoding="utf-8"))
    assert reparsed["experimental"]["press_enter_command"] is True
    assert reparsed["experimental"]["command_mode"] is False


# --- Privacy -----------------------------------------------------------------

def test_privacy_shows_db_path_and_counts(tmp_path):
    client, app_ref = _client(tmp_path)
    # Seed a few dictations.
    h = app_ref.history
    h.log(window_title="t", style="default", language="en", duration_ms=10,
          raw_text="hello", cleaned_text="hello")
    h.log(window_title="t", style="default", language="en", duration_ms=10,
          raw_text="world", cleaned_text="world")
    r = client.get("/settings/privacy", headers=HOST)
    assert r.status_code == 200
    assert b"Total dictations" in r.data
    assert b">2<" in r.data  # count rendered


def test_privacy_wipe_requires_confirm_token(tmp_path):
    client, app_ref = _client(tmp_path)
    app_ref.history.log(window_title="t", style="default", language="en",
                        duration_ms=1, raw_text="x", cleaned_text="x")
    r = client.post("/settings/privacy/wipe", headers=HOST, data={"confirm": "nope"})
    assert r.status_code == 302
    assert "WIPE" in r.headers["Location"]
    # Still there.
    n = app_ref.history.conn.execute("SELECT COUNT(*) FROM dictations").fetchone()[0]
    assert n == 1


def test_privacy_wipe_with_confirm_deletes(tmp_path):
    client, app_ref = _client(tmp_path)
    app_ref.history.log(window_title="t", style="default", language="en",
                        duration_ms=1, raw_text="x", cleaned_text="x")
    app_ref.history.log(window_title="t", style="default", language="en",
                        duration_ms=1, raw_text="y", cleaned_text="y")
    r = client.post("/settings/privacy/wipe", headers=HOST, data={"confirm": "WIPE"})
    assert r.status_code == 302
    n = app_ref.history.conn.execute("SELECT COUNT(*) FROM dictations").fetchone()[0]
    assert n == 0
