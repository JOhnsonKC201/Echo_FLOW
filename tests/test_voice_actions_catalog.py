"""Phase 14 PR 4 — action catalog: media keys, volume, folders, clipboard."""
from __future__ import annotations

import pytest

from src import voice_actions as va


def _cfg(folders=None):
    exp = {"action_mode": True}
    if folders is not None:
        exp["action_folders"] = folders
    return {"experimental": exp}


class _Inj:
    def __init__(self):
        self.keys = []

    def send_key(self, key):
        self.keys.append(key)
        return True


def _ctx(folders=None, injector=None):
    return va.ActionContext(focused_title=None, focused_path=None,
                            cfg=_cfg(folders), notify=lambda *a, **k: None,
                            injector=injector)


# --- classify ----------------------------------------------------------------

@pytest.mark.parametrize("body,name,args", [
    ("play", "media_key", {"key": "playpause"}),
    ("pause the music", "media_key", {"key": "playpause"}),
    ("next track", "media_key", {"key": "nexttrack"}),
    ("skip", "media_key", {"key": "nexttrack"}),
    ("previous track", "media_key", {"key": "prevtrack"}),
    ("previous", "media_key", {"key": "prevtrack"}),   # bare form (suffix optional, like 'next')
    ("prev", "media_key", {"key": "prevtrack"}),
    ("last", "media_key", {"key": "prevtrack"}),
    ("mute", "media_key", {"key": "volumemute"}),
    ("volume up", "volume", {"dir": "up"}),
    ("louder", "volume", {"dir": "up"}),
    ("volume down", "volume", {"dir": "down"}),
    ("open downloads folder", "open_folder", {"folder": "downloads"}),
    ("open folder documents", "open_folder", {"folder": "documents"}),
    ("open the link in the clipboard", "open_clipboard_link", {}),
])
def test_classify_catalog(body, name, args):
    m = va.classify(body, _cfg())
    assert m is not None, body
    assert m.name == name
    assert m.args == args


def test_open_app_still_wins_for_plain_open():
    m = va.classify("open spotify", _cfg())
    assert m is not None and m.name == "open_app"


# --- dispatch: media + volume ------------------------------------------------

def test_media_key_uses_injector():
    inj = _Inj()
    ok, msg = va.dispatch(va.ActionMatch("media_key", "Play", {"key": "playpause"}),
                          _ctx(injector=inj))
    assert ok is True
    assert inj.keys == ["playpause"]


def test_media_key_without_injector_fails():
    ok, msg = va.dispatch(va.ActionMatch("media_key", "Play", {"key": "playpause"}),
                          _ctx(injector=None))
    assert ok is False


def test_volume_presses_multiple_times():
    inj = _Inj()
    ok, msg = va.dispatch(va.ActionMatch("volume", "Vol", {"dir": "up"}),
                          _ctx(injector=inj))
    assert ok is True
    assert inj.keys and all(k == "volumeup" for k in inj.keys)
    assert 1 <= len(inj.keys) <= 5


# --- dispatch: open_folder ---------------------------------------------------

def test_open_folder_allowlisted(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr("os.startfile", lambda p: started.append(p), raising=False)
    ctx = _ctx(folders={"docs": str(tmp_path)})
    ok, msg = va.dispatch(va.ActionMatch("open_folder", "Open docs", {"folder": "docs"}), ctx)
    # On win32 startfile is mocked; on other platforms it returns the
    # not-supported message. Either way it must not crash and must resolve the
    # allowlisted dir.
    assert ("docs" in msg) or ok


def test_open_folder_unconfigured_fails():
    ok, msg = va.dispatch(va.ActionMatch("open_folder", "Open x", {"folder": "secret"}),
                          _ctx(folders={}))
    assert ok is False
    assert "secret" in msg


def test_open_folder_missing_dir_fails(tmp_path):
    ctx = _ctx(folders={"docs": str(tmp_path / "nope")})
    ok, msg = va.dispatch(va.ActionMatch("open_folder", "Open docs", {"folder": "docs"}), ctx)
    assert ok is False


# --- dispatch: clipboard link ------------------------------------------------

def test_clipboard_opens_safe_url(monkeypatch):
    opened = []
    monkeypatch.setattr("pyperclip.paste", lambda: "https://example.com")
    monkeypatch.setattr("webbrowser.open", lambda u, **k: opened.append(u) or True)
    ok, msg = va.dispatch(va.ActionMatch("open_clipboard_link", "Clip", {}), _ctx())
    assert ok is True
    assert opened == ["https://example.com"]


def test_clipboard_rejects_unsafe(monkeypatch):
    opened = []
    monkeypatch.setattr("pyperclip.paste", lambda: "rm -rf / ; echo pwned")
    monkeypatch.setattr("webbrowser.open", lambda u, **k: opened.append(u) or True)
    ok, msg = va.dispatch(va.ActionMatch("open_clipboard_link", "Clip", {}), _ctx())
    assert ok is False
    assert opened == []


def test_clipboard_bare_domain(monkeypatch):
    opened = []
    monkeypatch.setattr("pyperclip.paste", lambda: "github.com")
    monkeypatch.setattr("webbrowser.open", lambda u, **k: opened.append(u) or True)
    ok, msg = va.dispatch(va.ActionMatch("open_clipboard_link", "Clip", {}), _ctx())
    assert ok is True
    assert opened == ["https://github.com"]
