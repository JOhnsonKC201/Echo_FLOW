"""Phase 6 acceptance tests — Transforms CRUD + hotkey binding + Cleaner integration."""
from __future__ import annotations

import pytest
from urllib.parse import unquote_plus

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

ACCEPTED_HOTKEYS = [
    "ctrl+alt+p", "ctrl+shift+alt+1", "win+shift+f5",
    "command+alt+p", "option+shift+p",   # Mac spellings of cmd and alt
    "ctrl+shift",                        # modifier-only, as config.yaml uses
    "alt+win", "ctrl+alt",
    "ctrl+space", "ctrl+enter",          # named keys the listener already took
    "f9", "ctrl+f24",
    "Ctrl + Shift + P",                  # case and spacing are forgiving
]


@pytest.mark.parametrize("combo", ACCEPTED_HOTKEYS)
def test_validate_hotkey_accepts(combo):
    tf._validate_hotkey(combo)  # no raise


@pytest.mark.parametrize("combo", [
    "p", "ctrl+nope", "ctrl+ctrl+a", "ctrl+",
    "ctrl",        # one lone modifier would fire on every keystroke
    "ctrl+f25",    # pynput stops at f24
    "a+b", "ctrl+p+alt",
])
def test_validate_hotkey_rejects(combo):
    with pytest.raises(ValueError):
        tf._validate_hotkey(combo)


@pytest.mark.parametrize("combo", ACCEPTED_HOTKEYS)
def test_anything_that_validates_can_actually_register(combo):
    """The invariant that retires this whole class of bug.

    Validation and pynput conversion were separate grammars that disagreed in
    both directions: 'ctrl+shift' was refused despite registering fine, and
    'command+option+p' validated, reported success, then got dropped at
    registration because the converter did not know the macOS spellings. Tying
    the two together here means neither can drift again without failing.
    """
    from pynput.keyboard import HotKey
    canonical = tf._validate_hotkey(combo)
    pynput_combo = _transform_combo_to_pynput(canonical)
    assert pynput_combo is not None, f"{combo!r} validated but cannot register"
    HotKey.parse(pynput_combo)  # raises if pynput would reject it


def test_mac_spellings_survive_conversion():
    """Regression: these validated, flashed success, and never fired."""
    assert _transform_combo_to_pynput(tf._validate_hotkey("command+option+p")) \
        == "<alt>+<cmd>+p"


@pytest.mark.parametrize("a,b", [
    ("shift+ctrl+p", "ctrl+shift+p"),
    ("WIN+alt", "alt+win"),
    ("  ctrl + alt + 1  ", "ctrl+alt+1"),
])
def test_same_chord_has_one_canonical_spelling(a, b):
    assert tf._validate_hotkey(a) == tf._validate_hotkey(b)


def test_reordered_chord_counts_as_a_duplicate(tmp_path):
    """Storage is canonical, so 'shift+ctrl+p' collides with 'ctrl+shift+p'."""
    h = _h(tmp_path)
    tf.add_transform(h.conn, name="First", system_prompt="x", hotkey="ctrl+shift+p")
    with pytest.raises(ValueError):
        tf.add_transform(h.conn, name="Second", system_prompt="y",
                         hotkey="shift+ctrl+p")


# --- pynput combo conversion -------------------------------------------------

def test_combo_to_pynput_basic():
    assert _transform_combo_to_pynput("ctrl+alt+p") == "<ctrl>+<alt>+p"
    # Modifiers come out in hotkey_spec.MOD_ORDER (ctrl, alt, shift, cmd), not
    # in the order they were typed, so that one chord has exactly one spelling.
    # pynput matches on a set of keys, so ordering does not affect what fires.
    assert _transform_combo_to_pynput("win+shift+f5") == "<shift>+<cmd>+<f5>"
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
    def __init__(self, history, cfg=None):
        self.cfg = cfg or {"dashboard": {"host": "127.0.0.1", "port": 8766}}
        self.history = history
        self.refresh_calls = 0
    def refresh_transform_hotkeys(self):
        self.refresh_calls += 1


def _client(tmp_path, cfg=None):
    from src.dashboard.app import make_app
    h = _h(tmp_path)
    app_ref = _App(h, cfg)
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


HOST = {"Host": "127.0.0.1:8766"}

# config.yaml ships these; the reserved-chord check reads them from cfg.
CFG_WITH_HOTKEYS = {
    "dashboard": {"host": "127.0.0.1", "port": 8766, "open_hotkey": "<ctrl>+<cmd>"},
    "hotkey": {"combo": "ctrl+shift", "paste_last_combo": "ctrl+shift+win"},
    "prompt_engineering": {"enabled": True, "oneshot_combo": "ctrl+shift+alt"},
}


def _first_transform_id(client):
    client.get("/transforms", headers=HOST)  # seeds builtins
    return 1


def test_modifier_only_chord_binds(tmp_path):
    """The original bug: 'ctrl+alt' was refused despite registering fine."""
    client, app_ref = _client(tmp_path)
    tid = _first_transform_id(client)
    r = client.post("/transforms/bind-hotkey", headers=HOST,
                    data={"id": str(tid), "hotkey": "ctrl+alt"}, follow_redirects=False)
    assert "flash_kind=error" not in r.headers["Location"]
    page = client.get("/transforms", headers=HOST).data
    assert b"ctrl+alt" in page


def test_rejected_hotkey_keeps_what_you_typed(tmp_path):
    """A redirect used to drop the input, so a typo looked like a failed save."""
    client, _ = _client(tmp_path)
    tid = _first_transform_id(client)
    r = client.post("/transforms/bind-hotkey", headers=HOST,
                    data={"id": str(tid), "hotkey": "ctrl+nope"}, follow_redirects=False)
    loc = r.headers["Location"]
    assert "flash_kind=error" in loc
    assert "hk_val=ctrl%2Bnope" in loc
    # ...and the page echoes it back into that row's field.
    page = client.get(loc, headers=HOST).data
    assert b'value="ctrl+nope"' in page
    assert b"flash error" in page


def test_reserved_chord_is_refused_by_name(tmp_path):
    """'ctrl+shift' is the push-to-talk chord, so say that, not 'unsupported key'."""
    client, _ = _client(tmp_path, CFG_WITH_HOTKEYS)
    tid = _first_transform_id(client)
    r = client.post("/transforms/bind-hotkey", headers=HOST,
                    data={"id": str(tid), "hotkey": "ctrl+shift"}, follow_redirects=False)
    loc = r.headers["Location"]
    assert "flash_kind=error" in loc, "reserved chord was accepted"
    assert "push-to-talk" in unquote_plus(loc), f"message did not name the owner: {loc}"


def test_reserved_chords_reach_the_recorder(tmp_path):
    """The page ships the reserved map so the recorder can warn mid-press."""
    client, _ = _client(tmp_path, CFG_WITH_HOTKEYS)
    page = client.get("/transforms", headers=HOST).data.decode()
    assert "hk-reserved" in page
    assert "push-to-talk" in page
    # PE one-shot is "ctrl+shift+alt" in config; canonical order is ctrl, alt, shift.
    assert "ctrl+alt+shift" in page
