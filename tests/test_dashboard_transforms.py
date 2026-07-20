"""Phase 6 acceptance tests — Transforms CRUD + hotkey binding + Cleaner integration."""
from __future__ import annotations

import pytest

from src.history import History
from src.dashboard import transforms as tf
from src.cleanup import Cleaner
from src.main import _transform_combo_to_pynput


def _h(tmp_path):
    return History(str(tmp_path / "h.db"))


# --- CRUD --------------------------------------------------------------------

def test_seed_builtins_idempotent(tmp_path):
    h = _h(tmp_path)
    assert tf.seed_builtins(h.conn) == len(tf.BUILTINS)
    assert tf.seed_builtins(h.conn) == 0
    names = {t["name"] for t in tf.list_transforms(h.conn)}
    assert "Polish" in names and "Prompt Engineer" in names


def test_my_voice_builtin_seeds_and_is_protected(tmp_path):
    h = _h(tmp_path)
    tf.seed_builtins(h.conn)
    mv = next((t for t in tf.list_transforms(h.conn) if t["name"] == "My Voice"), None)
    assert mv is not None and mv["builtin"] is True
    with pytest.raises(ValueError):
        tf.delete_transform(h.conn, mv["id"])


def test_add_custom_transform(tmp_path):
    h = _h(tmp_path)
    tid = tf.add_transform(h.conn, name="Casual chat", system_prompt="Be brief.")
    assert tid > 0
    fetched = tf.get_transform(h.conn, tid)
    assert fetched["name"] == "Casual chat"
    assert fetched["builtin"] is False
    assert fetched["enabled"] is True


def test_add_rejects_duplicate_name(tmp_path):
    h = _h(tmp_path)
    tf.add_transform(h.conn, name="X", system_prompt="p")
    with pytest.raises(ValueError):
        tf.add_transform(h.conn, name="X", system_prompt="q")


def test_add_validates_empty_and_length(tmp_path):
    h = _h(tmp_path)
    with pytest.raises(ValueError):
        tf.add_transform(h.conn, name="", system_prompt="p")
    with pytest.raises(ValueError):
        tf.add_transform(h.conn, name="X", system_prompt="")
    with pytest.raises(ValueError):
        tf.add_transform(h.conn, name="x" * 61, system_prompt="p")


def test_delete_refuses_builtin(tmp_path):
    h = _h(tmp_path)
    tf.seed_builtins(h.conn)
    polish_id = next(t["id"] for t in tf.list_transforms(h.conn) if t["name"] == "Polish")
    with pytest.raises(ValueError):
        tf.delete_transform(h.conn, polish_id)


def test_delete_custom(tmp_path):
    h = _h(tmp_path)
    tid = tf.add_transform(h.conn, name="Tmp", system_prompt="p")
    assert tf.delete_transform(h.conn, tid) is True
    assert tf.get_transform(h.conn, tid) is None


def test_update_hotkey_and_disable(tmp_path):
    h = _h(tmp_path)
    tid = tf.add_transform(h.conn, name="T", system_prompt="p")
    tf.update_transform(h.conn, tid, hotkey="ctrl+alt+p", enabled=False)
    fetched = tf.get_transform(h.conn, tid)
    assert fetched["hotkey"] == "ctrl+alt+p"
    assert fetched["enabled"] is False


def test_update_clear_hotkey(tmp_path):
    h = _h(tmp_path)
    tid = tf.add_transform(h.conn, name="T", system_prompt="p", hotkey="ctrl+alt+1")
    tf.update_transform(h.conn, tid, hotkey=None)
    assert tf.get_transform(h.conn, tid)["hotkey"] is None


def test_update_refuses_duplicate_hotkey(tmp_path):
    h = _h(tmp_path)
    a = tf.add_transform(h.conn, name="A", system_prompt="p", hotkey="ctrl+alt+a")
    b = tf.add_transform(h.conn, name="B", system_prompt="p")
    with pytest.raises(ValueError):
        tf.update_transform(h.conn, b, hotkey="ctrl+alt+a")


def test_find_by_hotkey(tmp_path):
    h = _h(tmp_path)
    tid = tf.add_transform(h.conn, name="Z", system_prompt="p", hotkey="ctrl+alt+z")
    found = tf.find_by_hotkey(h.conn, "ctrl+alt+z")
    assert found and found["id"] == tid
    assert tf.find_by_hotkey(h.conn, "ctrl+alt+x") is None


def test_find_by_hotkey_ignores_disabled(tmp_path):
    h = _h(tmp_path)
    tid = tf.add_transform(h.conn, name="Z", system_prompt="p", hotkey="ctrl+alt+z")
    tf.update_transform(h.conn, tid, enabled=False)
    assert tf.find_by_hotkey(h.conn, "ctrl+alt+z") is None


# --- Hotkey validation -------------------------------------------------------

@pytest.mark.parametrize("combo", [
    "ctrl+alt+p", "ctrl+shift+alt+1", "win+shift+f5",
])
def test_validate_hotkey_accepts(combo):
    tf._validate_hotkey(combo)  # no raise


@pytest.mark.parametrize("combo", [
    "p", "ctrl+nope", "ctrl+ctrl+a", "ctrl+",
])
def test_validate_hotkey_rejects(combo):
    with pytest.raises(ValueError):
        tf._validate_hotkey(combo)


# --- pynput combo conversion -------------------------------------------------

def test_combo_to_pynput_basic():
    assert _transform_combo_to_pynput("ctrl+alt+p") == "<ctrl>+<alt>+p"
    assert _transform_combo_to_pynput("win+shift+f5") == "<cmd>+<shift>+<f5>"
    assert _transform_combo_to_pynput("") is None
    assert _transform_combo_to_pynput("nope+key") is None


# --- Cleaner system_prompt_override ------------------------------------------

def test_cleaner_uses_system_prompt_override(monkeypatch):
    c = Cleaner({"enabled": True, "provider": "ollama"})
    captured = {}
    def _fake_via(prompt, text, **k):
        captured["prompt"] = prompt
        return "ok"
    monkeypatch.setattr(c, "_via_ollama", _fake_via)
    out, skipped = c.clean("um yeah hello",
                            system_prompt_override="My custom prompt.")
    assert out == "ok"
    assert captured["prompt"] == "My custom prompt."


# --- Route round-trip --------------------------------------------------------

class _App:
    def __init__(self, history):
        self.cfg = {"dashboard": {"host": "127.0.0.1", "port": 8766}}
        self.history = history
        self.refresh_calls = 0
    def refresh_transform_hotkeys(self):
        self.refresh_calls += 1


def _client(tmp_path):
    from src.dashboard.app import make_app
    h = _h(tmp_path)
    app_ref = _App(h)
    return make_app(app_ref).test_client(), app_ref


def test_transforms_route_seeds_on_first_get(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.get("/transforms", headers={"Host": "127.0.0.1:8766"})
    assert r.status_code == 200
    assert b"Polish" in r.data
    assert b"built-in" in r.data


def test_transforms_route_add_post(tmp_path):
    client, app_ref = _client(tmp_path)
    client.post("/transforms/add", headers={"Host": "127.0.0.1:8766"},
                data={"name": "Brief", "system_prompt": "Be terse.", "hotkey": "ctrl+alt+b"})
    r = client.get("/transforms", headers={"Host": "127.0.0.1:8766"})
    assert b"Brief" in r.data
    assert app_ref.refresh_calls == 1
