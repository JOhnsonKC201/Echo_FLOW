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


def test_redact_passes_through_open_app():
    r = va.redact_args("open_app", {"app": "spotify"})
    assert r == {"app": "spotify"}
