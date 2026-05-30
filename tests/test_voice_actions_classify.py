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


# --- resolves(): prefix-free triggering gate ---------------------------------
# A bare verb may fire an action without the wake-word ONLY when it resolves to
# a configured app/folder or a valid URL/search; otherwise it must type normally.

def test_resolves_configured_app_true():
    cfg = _cfg(apps={"spotify": "spotify"})
    m = va.classify("open spotify", cfg)
    assert m.name == "open_app" and va.resolves(m, cfg) is True


def test_resolves_unconfigured_app_false():
    cfg = _cfg(apps={"spotify": "spotify"})
    m = va.classify("open the door and walk in", cfg)
    assert m.name == "open_app" and va.resolves(m, cfg) is False


def test_resolves_valid_url_true():
    cfg = _cfg()
    m = va.classify("go to github.com", cfg)
    assert m.name == "open_url" and va.resolves(m, cfg) is True


def test_resolves_web_search_true():
    cfg = _cfg()
    m = va.classify("search the web for cats", cfg)
    assert m.name == "web_search" and va.resolves(m, cfg) is True


def test_resolves_configured_folder_true():
    cfg = _cfg()
    cfg["experimental"]["action_folders"] = {"downloads": r"%USERPROFILE%\Downloads"}
    m = va.classify("open downloads folder", cfg)
    assert m.name == "open_folder" and va.resolves(m, cfg) is True


def test_resolves_unconfigured_folder_false():
    cfg = _cfg()
    m = va.classify("open projects folder", cfg)
    assert m.name == "open_folder" and va.resolves(m, cfg) is False


@pytest.mark.parametrize("text", ["play", "next", "mute", "volume up", "take a note that hi"])
def test_resolves_ambiguous_verbs_require_prefix(text):
    # Media/volume/note never fire prefix-free — they'd hijack common words.
    cfg = _cfg()
    m = va.classify(text, cfg)
    assert m is not None and va.resolves(m, cfg) is False


# --- Fuzzy prefix: tolerate a mis-heard wake word ----------------------------

def test_strip_prefix_fuzzy_catches_misheard_wakeword():
    # Whisper mangling "jarvis" → "zalvis"/"jervis" still strips to the command.
    assert cm.strip_prefix_fuzzy("zalvis open email", "jarvis") == "open email"
    assert cm.strip_prefix_fuzzy("Jervis, open email", "jarvis") == "open email"


def test_strip_prefix_fuzzy_exact_still_works():
    assert cm.strip_prefix_fuzzy("jarvis open spotify", "jarvis") == "open spotify"


def test_strip_prefix_fuzzy_ignores_unrelated_first_word():
    # A normal sentence must not look like a wake word.
    assert cm.strip_prefix_fuzzy("I love Paris in spring", "jarvis") is None
    assert cm.strip_prefix_fuzzy("Marcus opened the door", "jarvis") is None


def test_strip_prefix_fuzzy_needs_a_body():
    assert cm.strip_prefix_fuzzy("zalvis", "jarvis") is None


def test_strip_prefix_fuzzy_short_prefix_disabled():
    # Too-short prefixes are too collision-prone to fuzzy-match.
    assert cm.strip_prefix_fuzzy("cat open email", "go") is None


# --- Audit fixes: politeness, whitespace, multi-word prefix, validation ------

@pytest.mark.parametrize("text,app", [
    ("open spotify please", "spotify"),
    ("open spotify thanks", "spotify"),
    ("open  spotify", "spotify"),
])
def test_trailing_politeness_and_spaces_for_open(text, app):
    m = va.classify(text, _cfg(apps={"spotify": "spotify"}))
    assert m is not None and m.name == "open_app" and m.args == {"app": app}


def test_politeness_not_stripped_from_note_body():
    m = va.classify("take a note that call mom please", _cfg())
    assert m.name == "quick_note"
    assert m.args["body"] == "call mom please"   # free text kept intact


def test_multiword_app_name_collapses_spaces():
    cfg = _cfg(apps={"visual studio": "devenv.exe"})
    m = va.classify("open  visual   studio", cfg)
    assert m.args == {"app": "visual studio"}
    assert va.resolves(m, cfg) is True


def test_folder_with_trailing_politeness():
    cfg = _cfg()
    cfg["experimental"]["action_folders"] = {"downloads": r"%USERPROFILE%\Downloads"}
    m = va.classify("open downloads folder please", cfg)
    assert m.name == "open_folder" and m.args == {"folder": "downloads"}
    assert va.resolves(m, cfg) is True


def test_pause_with_politeness_matches_media():
    m = va.classify("pause please", _cfg())
    assert m is not None and m.name == "media_key"


def test_fuzzy_multiword_prefix_uses_exact_only():
    # Exact multi-word prefix still strips; the fuzzy fallback bails (no mis-strip).
    assert cm.strip_prefix_fuzzy("hey echo, open spotify", "hey echo") == "open spotify"
    # A misheard multi-word prefix simply doesn't fuzzy-match (returns None).
    assert cm.strip_prefix_fuzzy("hey eko open spotify", "hey echo") is None


def test_user_targets_collapses_config_key_spaces():
    cfg = _cfg(apps={"visual  studio": "devenv.exe"})
    targets = va.user_targets("app", cfg, None)
    assert "visual studio" in targets
