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
    # /settings/privacy is now a 302 redirect to /privacy (PR-E). Verified
    # separately in test_settings_privacy_redirects.
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
        "command_prefix": "computer",
    })
    assert r.status_code == 302
    reparsed = yaml.safe_load(app_ref.cfg_path.read_text(encoding="utf-8"))
    assert reparsed["experimental"]["press_enter_command"] is True
    assert reparsed["experimental"]["command_mode"] is False


@pytest.mark.parametrize("bad", ["", "a", "ab", "the", "now", "tell", "1234", "ok!"])
def test_experimental_save_rejects_bad_prefix(tmp_path, bad):
    client, _ = _client(tmp_path)
    r = client.post("/settings/experimental/save", headers=HOST, data={
        "command_prefix": bad,
    })
    assert r.status_code == 302
    # Either the validator caught it (flash about prefix) or it normalized to "computer".
    if bad == "":
        # Empty → defaulted to "computer", which is valid.
        return
    assert "command%20prefix" in r.headers["Location"]


# --- Experimental: intent-model fallback (tri-state select + conf floor) ------

def _reparse(app_ref):
    return yaml.safe_load(app_ref.cfg_path.read_text(encoding="utf-8"))


def test_experimental_save_intent_model_on(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/settings/experimental/save", headers=HOST, data={
        "command_prefix": "computer",
        "action_intent_model": "on",
        "action_intent_min_conf": "0.8",
    })
    assert r.status_code == 302
    exp = _reparse(app_ref)["experimental"]
    assert exp["action_intent_model"] is True
    assert exp["action_intent_min_conf"] == 0.8


def test_experimental_save_intent_model_shadow(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/settings/experimental/save", headers=HOST, data={
        "command_prefix": "computer",
        "action_intent_model": "shadow",
    })
    assert r.status_code == 302
    assert _reparse(app_ref)["experimental"]["action_intent_model"] == "shadow"


def test_experimental_save_intent_off_writes_real_bool(tmp_path):
    # Critical: "off" must persist a YAML boolean false, NOT the string "false"
    # (a non-empty string is truthy and would read as ON in main._do_dictation).
    client, app_ref = _client(tmp_path)
    r = client.post("/settings/experimental/save", headers=HOST, data={
        "command_prefix": "computer",
        "action_intent_model": "off",
    })
    assert r.status_code == 302
    assert _reparse(app_ref)["experimental"]["action_intent_model"] is False


@pytest.mark.parametrize("bad_conf", ["1.5", "-0.1", "abc"])
def test_experimental_save_rejects_bad_conf(tmp_path, bad_conf):
    client, _ = _client(tmp_path)
    r = client.post("/settings/experimental/save", headers=HOST, data={
        "command_prefix": "computer",
        "action_intent_model": "on",
        "action_intent_min_conf": bad_conf,
    })
    assert r.status_code == 302
    assert "intent%20confidence" in r.headers["Location"]


def test_experimental_get_renders_intent_controls(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/settings/experimental", headers=HOST)
    assert r.status_code == 200
    assert b'name="action_intent_model"' in r.data
    assert b'name="action_intent_min_conf"' in r.data
    assert b"Shadow" in r.data


# --- My Voice (humanize) tri-state ------------------------------------------

def test_humanize_save_tristate(tmp_path):
    client, app_ref = _client(tmp_path)
    for choice, expected in [("on", True), ("shadow", "shadow"), ("off", False)]:
        r = client.post("/settings/experimental/save", headers=HOST, data={
            "command_prefix": "computer", "humanize": choice,
        })
        assert r.status_code == 302
        val = _reparse(app_ref)["experimental"]["humanize"]
        assert val == expected
        # "off" must be a real YAML bool, never the truthy string "false".
        if choice == "off":
            assert val is False


def test_humanize_save_extras_and_bounds(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/settings/experimental/save", headers=HOST, data={
        "command_prefix": "computer", "humanize": "on",
        "humanize_use_cloud": "1", "humanize_log_verbose": "1",
        "humanize_min_sim": "0.7",
    })
    assert r.status_code == 302
    exp = _reparse(app_ref)["experimental"]
    assert exp["humanize_use_cloud"] is True
    assert exp["humanize_log_verbose"] is True
    assert exp["humanize_min_sim"] == 0.7
    # out-of-range similarity is rejected
    r2 = client.post("/settings/experimental/save", headers=HOST, data={
        "command_prefix": "computer", "humanize_min_sim": "1.9",
    })
    assert "similarity" in r2.headers["Location"]


def test_experimental_get_renders_humanize_controls(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/settings/experimental", headers=HOST)
    assert b'name="humanize"' in r.data
    assert b'name="humanize_min_sim"' in r.data
    assert b'name="humanize_use_cloud"' in r.data


# --- Privacy (PR-E: moved to top-level /privacy) ----------------------------

def test_settings_privacy_redirects_to_top_level(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/settings/privacy", headers=HOST, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/privacy")


def test_humanizer_paste_in_knobs_save(tmp_path):
    """The paste-in humanizer carries its own model and meaning floor, separate
    from the dictation pass — those are for different jobs."""
    client, app_ref = _client(tmp_path)
    r = client.post("/settings/experimental/save", headers=HOST, data={
        "command_prefix": "computer", "humanize": "off",
        "humanize_text_model": "qwen2.5:7b-instruct",
        "humanize_text_min_sim": "0.5",
    })
    assert r.status_code == 302
    exp = _reparse(app_ref)["experimental"]
    assert exp["humanize_text_model"] == "qwen2.5:7b-instruct"
    assert exp["humanize_text_min_sim"] == 0.5


def test_humanizer_model_rejects_a_command_string(tmp_path):
    """The value reaches an HTTP payload, not a shell — but a name with spaces
    is a mistake worth catching at the boundary rather than at call time."""
    client, app_ref = _client(tmp_path)
    r = client.post("/settings/experimental/save", headers=HOST, data={
        "command_prefix": "computer", "humanize": "off",
        "humanize_text_model": "qwen2.5 && rm -rf /",
    })
    assert r.status_code == 302
    assert "flash=" in r.headers["Location"]
    assert not _reparse(app_ref)["experimental"].get("humanize_text_model")


def test_humanizer_meaning_floor_is_bounded(tmp_path):
    client, app_ref = _client(tmp_path)
    before = _reparse(app_ref)["experimental"].get("humanize_text_min_sim")
    for bad in ["2.5", "-1", "not-a-number"]:
        r = client.post("/settings/experimental/save", headers=HOST, data={
            "command_prefix": "computer", "humanize": "off",
            "humanize_text_min_sim": bad,
        })
        assert "flash=" in r.headers["Location"], bad
        # Rejected outright — the stored floor is left exactly as it was.
        assert _reparse(app_ref)["experimental"].get(
            "humanize_text_min_sim") == before, bad


def test_experimental_get_renders_paste_in_humanizer_controls(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/settings/experimental", headers=HOST)
    assert b'name="humanize_text_model"' in r.data
    assert b'name="humanize_text_min_sim"' in r.data
