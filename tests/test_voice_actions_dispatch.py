"""Phase 14 — Action Mode dispatch: each handler hits the right primitive with
sanitized args and never raises."""
from __future__ import annotations

import pytest

from src import voice_actions as va


def _ctx(apps=None):
    cfg = {"experimental": {"action_apps": apps or {}}}
    return va.ActionContext(focused_title=None, focused_path=None,
                            cfg=cfg, notify=lambda *a, **k: None)


# --- open_url ----------------------------------------------------------------

def test_open_url_calls_webbrowser(monkeypatch):
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda u, **k: opened.append(u) or True)
    m = va.ActionMatch("open_url", "Open", {"url": "https://example.com"})
    ok, msg = va.dispatch(m, _ctx())
    assert ok is True
    assert opened == ["https://example.com"]


@pytest.mark.parametrize("bad", [
    "file:///etc/passwd",
    "javascript:alert(1)",
    "https://example.com && rm -rf /",
    "ftp://example.com",
])
def test_open_url_rejects_unsafe_schemes_and_metachars(monkeypatch, bad):
    called = []
    monkeypatch.setattr("webbrowser.open", lambda u, **k: called.append(u) or True)
    m = va.ActionMatch("open_url", "Open", {"url": bad})
    ok, msg = va.dispatch(m, _ctx())
    assert ok is False
    assert called == []   # never reached the browser


# --- web_search --------------------------------------------------------------

def test_web_search_urlencodes_query(monkeypatch):
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda u, **k: opened.append(u) or True)
    m = va.ActionMatch("web_search", "Search", {"query": "best pizza & beer"})
    ok, msg = va.dispatch(m, _ctx())
    assert ok is True
    assert opened == ["https://www.google.com/search?q=best+pizza+%26+beer"]


# --- open_app ----------------------------------------------------------------

def test_open_app_launches_configured_exe_no_shell(monkeypatch):
    calls = {}

    def fake_popen(args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return object()

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    m = va.ActionMatch("open_app", "Open notepad", {"app": "notepad"})
    ok, msg = va.dispatch(m, _ctx(apps={"notepad": "notepad.exe"}))
    assert ok is True
    # Launched as a list (no shell string) with shell explicitly off.
    assert isinstance(calls["args"], list)
    assert calls["args"][0].lower().endswith("notepad.exe")
    assert calls["kwargs"].get("shell") is False


def test_open_app_url_target_uses_browser(monkeypatch):
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda u, **k: opened.append(u) or True)
    m = va.ActionMatch("open_app", "Open browser", {"app": "browser"})
    ok, msg = va.dispatch(m, _ctx(apps={"browser": "https://www.google.com"}))
    assert ok is True
    assert opened == ["https://www.google.com"]


def test_open_app_unconfigured_fails_cleanly(monkeypatch):
    called = {"popen": False}
    monkeypatch.setattr("subprocess.Popen",
                        lambda *a, **k: called.__setitem__("popen", True))
    m = va.ActionMatch("open_app", "Open spotify", {"app": "spotify"})
    ok, msg = va.dispatch(m, _ctx(apps={}))
    assert ok is False
    assert "spotify" in msg
    assert called["popen"] is False


# --- robustness --------------------------------------------------------------

def test_dispatch_unknown_handler_returns_false():
    m = va.ActionMatch("does_not_exist", "Nope", {})
    ok, msg = va.dispatch(m, _ctx())
    assert ok is False


def test_handler_exception_is_swallowed(monkeypatch):
    def boom(u, **k):
        raise RuntimeError("browser exploded")
    monkeypatch.setattr("webbrowser.open", boom)
    m = va.ActionMatch("open_url", "Open", {"url": "https://example.com"})
    ok, msg = va.dispatch(m, _ctx())
    assert ok is False
    assert "exploded" in msg or "Couldn't" in msg
