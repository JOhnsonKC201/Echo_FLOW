"""Regression tests for the system-audit fixes (2026-06-03).

Each test pins one concrete bug found during the full-system audit:
  - hotkey listener survives a raising callback (no silent dictation death)
  - learned-casing capture handles curly apostrophes (U+2019)
  - the dashboard scratchpad-target route rejects open-redirect `back` values
"""
from __future__ import annotations


# --- hotkey: a raising callback must not escape into pynput's thread ---------

def test_hotkey_safe_swallows_callback_exception():
    from src.hotkey import HotkeyListener

    def boom():
        raise RuntimeError("recorder.start blew up (mic unplugged)")

    # Must NOT raise — if it did, the pynput listener thread would die and
    # dictation would silently stop with no indicator.
    HotkeyListener._safe(boom, "activate")
    HotkeyListener._safe(None, "deactivate")  # None callback is a no-op


def test_hotkey_safe_runs_callback():
    from src.hotkey import HotkeyListener
    calls = []
    HotkeyListener._safe(lambda: calls.append(1), "activate")
    assert calls == [1]


# --- learn: curly-apostrophe possessives carry meaningful casing ------------

def test_meaningful_casing_handles_curly_apostrophe():
    from src.learn import _meaningful_casing

    assert _meaningful_casing("TikTok's") is True       # ASCII apostrophe
    assert _meaningful_casing("TikTok’s") is True   # curly U+2019 (Whisper)
    assert _meaningful_casing("London’s") is True
    # Plain lowercase still carries nothing worth learning.
    assert _meaningful_casing("hello") is False
    assert _meaningful_casing("rocks’") is False


# --- dashboard: scratchpad target `back` must stay same-site ----------------

def _client(tmp_path):
    import types
    from src.history import History
    from src.dashboard.app import make_app
    h = History(str(tmp_path / "h.db"))
    app_ref = types.SimpleNamespace(
        cfg={"dashboard": {"host": "127.0.0.1", "port": 8766, "theme": "dark"}},
        history=h, pattern_miner=None, cleaner=None,
    )
    return make_app(app_ref).test_client()


def test_scratchpad_target_rejects_open_redirect(tmp_path):
    client = _client(tmp_path)
    hdr = {"Host": "127.0.0.1:8766"}
    # An absolute URL in `back` must not produce an off-site 302.
    r = client.post("/scratchpad/target",
                    data={"id": "0", "back": "http://evil.example"},
                    headers=hdr, follow_redirects=False)
    assert r.status_code in (302, 303)
    loc = r.headers.get("Location", "")
    assert "evil.example" not in loc
    assert loc.startswith("/scratchpad")


def test_scratchpad_target_rejects_protocol_relative(tmp_path):
    client = _client(tmp_path)
    hdr = {"Host": "127.0.0.1:8766"}
    r = client.post("/scratchpad/target",
                    data={"id": "0", "back": "//evil.example"},
                    headers=hdr, follow_redirects=False)
    assert r.status_code in (302, 303)
    loc = r.headers.get("Location", "")
    assert "evil.example" not in loc
    assert loc.startswith("/scratchpad")
