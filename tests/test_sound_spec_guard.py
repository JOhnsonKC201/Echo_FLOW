"""Sound cue values are stat'ed and played by path, and touching a network
path sends the user's NetNTLM hash. So a cue value is checked with
sound.is_safe_spec() in three places: when settings are saved, when the
Test button previews it, and inside sound.py right before playback."""
from __future__ import annotations

import os

import pytest
import yaml

from src import sound
from tests.test_dashboard_settings import HOST, _client
from tests.test_unc_guard import UNC_SPELLINGS

pytestmark = pytest.mark.unit

CATALOG = [v for v, _ in sound.SOUND_CHOICES + sound.MAC_SOUND_CHOICES]
UNC_WAVS = [p.rstrip("\\/") + "\\cue.wav" for p in UNC_SPELLINGS]


# --- the rule -----------------------------------------------------------------

@pytest.mark.parametrize("spec", ["", "   "] + CATALOG)
def test_blank_and_every_catalog_choice_are_allowed(spec):
    assert sound.is_safe_spec(spec)


@pytest.mark.parametrize("spec", [
    r"C:\Users\me\Music\beep.wav",
    "C:/Users/me/Music/beep.wav",
    "/Users/me/Library/Sounds/beep.aiff",
    "my own cue.wav",
])
def test_a_local_sound_file_is_allowed(spec):
    assert sound.is_safe_spec(spec)


@pytest.mark.parametrize("spec", UNC_WAVS)
def test_network_paths_are_refused(spec):
    assert not sound.is_safe_spec(spec)


@pytest.mark.parametrize("spec", ["ding" + chr(0) + ".wav", "ding" + chr(10) + ".wav"])
def test_control_characters_are_refused(spec):
    assert not sound.is_safe_spec(spec)


# --- where it is enforced -----------------------------------------------------

@pytest.fixture
def stat_calls(monkeypatch):
    # Recorded, not raised: playback swallows exceptions, so a raising stub
    # would make a guard-less playback "pass" by failing for another reason.
    calls = []
    monkeypatch.setattr(os.path, "exists", lambda p: calls.append(p) or False)
    return calls


@pytest.mark.parametrize("spec", UNC_WAVS[:3])
def test_playback_refuses_before_touching_the_path(spec, stat_calls):
    assert sound._play_alias_or_file(spec) is False
    # Only the cue's own path matters: on CI the refusal's log line makes the
    # logging handler stat its own log file, which is not a leak.
    assert [p for p in stat_calls if "host" in str(p)] == []


@pytest.mark.parametrize("spec", UNC_WAVS[:3])
def test_preview_route_refuses_without_playing(spec, tmp_path, monkeypatch):
    client, _ = _client(tmp_path)
    played = []
    monkeypatch.setattr(sound, "preview", lambda a: played.append(a) or True)
    r = client.post("/settings/system/sound-preview", headers=HOST, json={"alias": spec})
    assert r.get_json()["ok"] is False
    assert played == []


@pytest.mark.parametrize("spec", UNC_WAVS[:3])
def test_settings_save_refuses_and_keeps_the_old_value(spec, tmp_path):
    client, app_ref = _client(tmp_path)
    before = yaml.safe_load(app_ref.cfg_path.read_text(encoding="utf-8"))["sound"]
    r = client.post("/settings/system/save", headers=HOST, data={
        "sound_enabled": "1", "silence_timeout_ms": "2500",
        "sound_start_alias": spec,
    })
    assert r.status_code == 302
    after = yaml.safe_load(app_ref.cfg_path.read_text(encoding="utf-8"))["sound"]
    assert after.get("start_alias") == before.get("start_alias")


def test_settings_save_still_accepts_a_catalog_choice(tmp_path):
    client, app_ref = _client(tmp_path)
    client.post("/settings/system/save", headers=HOST, data={
        "sound_enabled": "1", "silence_timeout_ms": "2500",
        "sound_start_alias": "chimes.wav",
    })
    after = yaml.safe_load(app_ref.cfg_path.read_text(encoding="utf-8"))["sound"]
    assert after["start_alias"] == "chimes.wav"


@pytest.mark.parametrize("spec", [r"..\..\Users\me\secret.wav", "../../etc/x.wav",
                                  "x" * 261 + ".wav"])
def test_traversal_and_overlong_values_are_refused(spec):
    assert not sound.is_safe_spec(spec)
