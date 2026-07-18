"""Phase 14 — Action Mode security hardening (SEC-1/2/3/7)."""
from __future__ import annotations

import pytest

from src import voice_actions as va


# --- _is_safe_url: accepts legitimate URLs ----------------------------------

@pytest.mark.parametrize("url", [
    "https://example.com",
    "http://docs.python.org/3/",
    "https://example.com/path?q=1",
    "mailto:me@example.com",
])
def test_safe_urls_pass(url):
    assert va._is_safe_url(url) is True


# --- _is_safe_url: rejects abuse (SEC-1 / SEC-2) ----------------------------

@pytest.mark.parametrize("url", [
    "https://a:b@evil.com",            # SEC-1 userinfo spoofing
    "https://user@evil.com",           # SEC-1 userinfo (username only)
    "http://exаmple.com",         # SEC-1 IDN homograph (cyrillic 'а')
    "https://x.com/%0aSet-Cookie",     # SEC-1 percent-encoded control char
    "ftp://example.com",               # bad scheme
    "file:///etc/passwd",              # bad scheme
    "javascript:alert(1)",             # bad scheme
    "https://example.com && rm -rf /", # space + metachar
    "mailto:a@b.com?subject=x",        # SEC-2 mailto header injection
    "mailto:a@b.com?body=secret",      # SEC-2
    "mailto:a@b.com?attach=/etc/passwd",  # SEC-2
    "mailto:a@b@evil.com",             # SEC-2 multiple '@'
    "mailto:a@еxample.com",            # SEC-2 IDN homograph (cyrillic 'е')
])
def test_unsafe_urls_rejected(url):
    assert va._is_safe_url(url) is False


# --- _domain_to_url / _RE_DOMAIN (SEC-7) ------------------------------------

@pytest.mark.parametrize("token,expected", [
    ("github.com", "https://github.com"),
    ("docs.python.org/3/", "https://docs.python.org/3"),  # trailing slash stripped
])
def test_domain_resolves(token, expected):
    assert va._domain_to_url(token) == expected


@pytest.mark.parametrize("token", [
    "github",                 # no dot → an app name, not a site
    "exаmple.com",       # non-ASCII (homograph) → rejected by re.ASCII
    "open spotify",           # contains a space
])
def test_domain_not_navigated(token):
    assert va._domain_to_url(token) is None


# --- redact_args (SEC-3) ----------------------------------------------------

def test_redact_search_query():
    r = va.redact_args("web_search", {"query": "my secret search"})
    assert "secret" not in r["query"]
    assert "len=" in r["query"]


def test_redact_url_to_host_only():
    r = va.redact_args("open_url", {"url": "https://example.com/secret/path?token=abc"})
    assert r["url"] == "https://example.com"


def test_redact_note_body():
    r = va.redact_args("quick_note", {"body": "buy milk and call mom"})
    assert "milk" not in r["body"]


_APP_CFG = {"experimental": {"action_apps": {"spotify": "spotify.exe"}}}
_FOLDER_CFG = {"experimental": {"action_folders": {"downloads": "C:/x/Downloads"}}}


def test_redact_passes_through_open_app():
    # A name that IS a configured target is a config key the user chose, not
    # spoken content — it stays readable on the dashboard.
    r = va.redact_args("open_app", {"app": "spotify"}, _APP_CFG)
    assert r == {"app": "spotify"}


# An app/folder slot is only an allowlist key once it PROVABLY matches one.
# classify()'s `^open (.+)$` catch-all puts arbitrary speech in args["app"] and
# defers the allowlist to dispatch, so an unconfigured name is still free text
# and must not reach the log/DB verbatim.

def test_redact_args_redacts_unconfigured_app_name():
    r = va.redact_args("open_app", {"app": "my divorce lawyer meeting notes"},
                       _APP_CFG)
    assert "divorce" not in str(r)


def test_redact_label_redacts_unconfigured_app_name():
    m = va.classify("open my divorce lawyer meeting notes for the affair case",
                    _APP_CFG)
    assert m.name == "open_app"          # the catch-all really does fire
    lbl = va.redact_label(m.name, m.label, m.args, _APP_CFG)
    assert "divorce" not in (lbl or "")


def test_redact_redacts_unconfigured_folder_name():
    m = va.classify("open the my 2019 tax audit documents folder", _FOLDER_CFG)
    assert m.name == "open_folder"
    assert "tax" not in (va.redact_label(m.name, m.label, m.args, _FOLDER_CFG) or "")
    assert "tax" not in str(va.redact_args(m.name, m.args, _FOLDER_CFG))


def test_redact_open_target_without_cfg_is_failsafe():
    # Without a cfg we cannot prove the name is a config key. A caller that
    # forgets to pass one must get the SAFE behavior, never the leak.
    assert "spotify" not in str(va.redact_args("open_app", {"app": "spotify"}))
    assert "spotify" not in (va.redact_label("open_app", "Open spotify",
                                             {"app": "spotify"}) or "")


# --- redact_label (SEC-3 companion) ------------------------------------------
# The human label can re-leak exactly what redact_args just removed ("Search
# the web for "my secret"") — the log site must run it through redact_label
# whenever verbose logging is off.

def test_redact_label_web_search_drops_query():
    lbl = va.redact_label("web_search", "Search the web for “my secret plans”",
                          {"query": "my secret plans"})
    assert "secret" not in lbl
    assert lbl == "Search the web"


def test_redact_label_open_url_host_only():
    lbl = va.redact_label("open_url", "Open https://example.com/secret?token=abc",
                          {"url": "https://example.com/secret?token=abc"})
    assert lbl == "Open https://example.com"


def test_redact_label_draft_event_drops_details():
    lbl = va.redact_label("draft_event", "Draft event: dentist appointment",
                          {"details": "dentist appointment"})
    assert "dentist" not in lbl
    assert lbl == "Draft event"


def test_redact_label_quick_note_stays_generic():
    assert va.redact_label("quick_note", "Take a note", {"body": "x"}) == "Take a note"


def test_redact_label_passes_through_safe_handlers():
    # A CONFIGURED app name is an allowlist key, not free content; the fixed
    # labels (media/volume/summarize/clipboard) carry no slot at all.
    assert va.redact_label("open_app", "Open spotify", {"app": "spotify"},
                           _APP_CFG) == "Open spotify"
    assert va.redact_label("media_key", "Play / pause", {"key": "playpause"}) == "Play / pause"
