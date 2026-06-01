"""Shared pytest fixtures. Tests must not require network — Echo Flow is local-only."""
import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

# Make `src` importable as a package from the project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# --- Stub leaf native/I-O shims when absent --------------------------------
# Echo Flow's runtime pulls in platform shims (clipboard, input, audio, tray,
# mDNS, toast). Pure-logic tests don't exercise them, but importing modules
# like ``src.main`` transitively imports them — so on a headless box missing
# PortAudio the suite fails to *collect*. We register minimal stand-ins for the
# dependency-free shim libraries only, and never for libraries whose behavior a
# test asserts (flask, numpy, faster-whisper, sentence-transformers). When the
# real library is installed it is used unchanged.

def _stub_if_missing(name: str, build) -> None:
    try:
        __import__(name)
        return  # real library present — leave it alone
    except Exception:
        pass
    mod = build()
    mod.__stubbed_for_tests__ = True
    sys.modules[name] = mod


def _mod(name: str) -> types.ModuleType:
    return types.ModuleType(name)


def _build_pyperclip():
    m = _mod("pyperclip")
    m.copy = lambda *a, **k: None
    m.paste = lambda *a, **k: ""
    return m


def _build_pyautogui():
    m = _mod("pyautogui")
    for fn in ("hotkey", "press", "typewrite", "write", "click", "keyDown", "keyUp"):
        setattr(m, fn, lambda *a, **k: None)
    return m


def _build_sounddevice():
    m = _mod("sounddevice")

    class _InputStream:  # pragma: no cover - never started in unit tests
        def __init__(self, *a, **k): ...
        def start(self): ...
        def stop(self): ...
        def close(self): ...

    m.InputStream = _InputStream
    m.query_devices = lambda *a, **k: []
    return m


def _build_pynput():
    """A *faithful* pynput.keyboard stub: stable per-name key identities and
    real f-key validation, so hotkey-parsing tests give correct results without
    the real library. Distinct names → distinct stable sentinels; f1..f20 are
    valid, anything else raises AttributeError (mirrors pynput)."""
    m = _mod("pynput")
    kb = types.ModuleType("pynput.keyboard")

    class _Sentinel:
        __slots__ = ("name",)
        def __init__(self, name): self.name = name
        def __repr__(self): return f"Key.{self.name}"

    _VALID = {
        "ctrl", "alt", "shift", "cmd", "space", "enter", "tab", "esc",
        "ctrl_l", "ctrl_r", "alt_l", "alt_r", "alt_gr", "shift_l", "shift_r",
    }

    class _Key:
        _cache: dict = {}
        def __getattr__(self, name):
            ok = name in _VALID or (
                name.startswith("f") and name[1:].isdigit() and 1 <= int(name[1:]) <= 20
            )
            if not ok:
                raise AttributeError(name)
            return self._cache.setdefault(name, _Sentinel(name))

    class _KeyCode:
        @staticmethod
        def from_char(c):
            return ("char", c)  # stable by value → set membership works

    class _Listener:  # pragma: no cover - never run in unit tests
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def join(self): ...
        def stop(self): ...

    kb.Key = _Key()
    kb.KeyCode = _KeyCode
    kb.Listener = _Listener
    m.keyboard = kb
    sys.modules["pynput.keyboard"] = kb
    return m


def _build_pystray():
    m = _mod("pystray")
    m.Icon = lambda *a, **k: None
    m.Menu = lambda *a, **k: None
    m.MenuItem = lambda *a, **k: None
    return m


def _build_zeroconf():
    m = _mod("zeroconf")
    for n in ("Zeroconf", "ServiceInfo", "ServiceBrowser"):
        setattr(m, n, type(n, (), {"__init__": lambda self, *a, **k: None}))
    return m


_stub_if_missing("pyperclip", _build_pyperclip)
_stub_if_missing("pyautogui", _build_pyautogui)
_stub_if_missing("sounddevice", _build_sounddevice)
_stub_if_missing("pynput", _build_pynput)
_stub_if_missing("pystray", _build_pystray)
_stub_if_missing("zeroconf", _build_zeroconf)
_stub_if_missing("winsdk", lambda: _mod("winsdk"))


@pytest.fixture
def temp_db(tmp_path):
    """A clean SQLite DB with the dictations schema, in a temp dir."""
    from src.history import History
    db_path = tmp_path / "test_history.db"
    h = History(str(db_path))
    yield h, str(db_path)
    try:
        h.conn.close()
    except Exception:
        pass


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """Run tests with no cloud API keys leaking in (Echo Flow is local-only)."""
    for k in ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)
