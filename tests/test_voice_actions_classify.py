"""Phase 14 — Action Mode classifier + prefix grammar."""
from __future__ import annotations

import pytest

from src import commands as cm
from src import voice_actions as va


def _cfg(apps=None, email_url=None):
    exp = {"command_prefix": "computer", "action_mode": True}
    if apps is not None:
        exp["action_apps"] = apps
    if email_url is not None:
        exp["action_email_url"] = email_url
    return {"experimental": exp}


# --- Prefix stripping: parity with commands.strip_prefix ---------------------

@pytest.mark.parametrize("text", [
    "computer, open spotify",
    "Computer open my email",
    "computer: search the web for cats",
    "no prefix here",
    "",
    "computer",
])
def test_strip_prefix_parity(text):
    assert va.strip_prefix(text, "computer") == cm.strip_prefix(text, "computer")


def test_strip_prefix_custom_word():
    assert va.strip_prefix("hey echo, open spotify", "hey echo") == "open spotify"


# --- open_app ----------------------------------------------------------------

def test_open_spotify_matches_open_app():
    m = va.classify("open spotify", _cfg(apps={"spotify": "spotify"}))
    assert m is not None
    assert m.name == "open_app"
    assert m.args == {"app": "spotify"}


def test_open_app_matches_even_when_not_configured():
    # classify validates shape, not existence — dispatch reports the missing app.
    m = va.classify("open spotify", _cfg(apps={}))
    assert m is not None
    assert m.name == "open_app"
    assert m.args == {"app": "spotify"}


def test_open_app_trailing_punctuation_stripped():
    m = va.classify("open notepad.", _cfg(apps={"notepad": "notepad.exe"}))
    assert m is not None and m.name == "open_app"
    assert m.args == {"app": "notepad"}


# --- web_search --------------------------------------------------------------

@pytest.mark.parametrize("body", [
    "search google for cats",
    "search the web for cats",
    "search for cats",
])
def test_web_search_variants(body):
    m = va.classify(body, _cfg())
    assert m is not None and m.name == "web_search"
    assert m.args == {"query": "cats"}


def test_web_search_multiword_query():
    m = va.classify("search the web for best pizza in town", _cfg())
    assert m is not None and m.name == "web_search"
    assert m.args == {"query": "best pizza in town"}


# --- open_url ----------------------------------------------------------------

def test_open_email_uses_configured_url():
    m = va.classify("open my email", _cfg(email_url="https://mail.proton.me"))
    assert m is not None and m.name == "open_url"
    assert m.args == {"url": "https://mail.proton.me"}


def test_open_email_default_url():
    m = va.classify("open email", _cfg())
    assert m is not None and m.name == "open_url"
    assert m.args["url"] == "https://mail.google.com"


@pytest.mark.parametrize("body,expected", [
    ("open github.com", "https://github.com"),
    ("go to docs.python.org", "https://docs.python.org"),
    ("go to example.com/path", "https://example.com/path"),
])
def test_domain_opens_as_url(body, expected):
    m = va.classify(body, _cfg(apps={}))
    assert m is not None and m.name == "open_url"
    assert m.args == {"url": expected}


def test_go_to_non_site_is_not_an_action():
    # "go to the top" is a Command Mode concept, not a URL — no action match.
    assert va.classify("go to the top", _cfg()) is None


# --- Safety / no shell exec --------------------------------------------------

def test_injection_attempt_resolves_to_missing_app_not_shell(monkeypatch):
    # "open spotify && rm -rf /" must become an app-key lookup that fails — the
    # raw string is never handed to a shell.
    body = "open spotify && rm -rf /"
    m = va.classify(body, _cfg(apps={"spotify": "spotify"}))
    assert m is not None and m.name == "open_app"
    # The whole spoken remainder is the key; it is not "spotify".
    assert m.args["app"] != "spotify"

    called = {"popen": False, "startfile": False}
    import subprocess
    monkeypatch.setattr(subprocess, "Popen",
                        lambda *a, **k: called.__setitem__("popen", True))
    monkeypatch.setattr("os.startfile", lambda *a, **k: called.__setitem__("startfile", True),
                        raising=False)
    ctx = va.ActionContext(focused_title=None, focused_path=None,
                           cfg=_cfg(apps={"spotify": "spotify"}), notify=lambda *a, **k: None)
    ok, msg = va.dispatch(m, ctx)
    assert ok is False
    assert called == {"popen": False, "startfile": False}


def test_classify_empty_and_none():
    assert va.classify("", _cfg()) is None
    assert va.classify(None, _cfg()) is None


# --- list_supported ----------------------------------------------------------

def test_list_supported_non_empty():
    labels = va.list_supported(_cfg(apps={"spotify": "spotify", "notepad": "notepad.exe"}))
    assert any("Search the web" in s for s in labels)
    assert any("spotify" in s for s in labels)
