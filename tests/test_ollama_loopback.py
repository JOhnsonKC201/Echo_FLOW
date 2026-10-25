"""Talk to Ollama on 127.0.0.1, not 'localhost'.

On Windows 'localhost' resolves to ::1 first, and Ollama listens on IPv4 only,
so every NEW connection waited for the ::1 attempt to fail before falling back.
Measured on this machine: 2,045 ms per new connection to localhost, 1 ms to
127.0.0.1. A dictation pays it whenever the pooled connection has gone idle,
i.e. on the first dictation after a break, which is exactly when it hurts."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src import ollama_supervisor as osup
from src.cleanup import Cleaner

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("given,expected", [
    ("http://localhost:11434", "http://127.0.0.1:11434"),
    ("http://LOCALHOST:11434/", "http://127.0.0.1:11434"),
    ("http://localhost", "http://127.0.0.1"),
    ("http://127.0.0.1:11434", "http://127.0.0.1:11434"),
    ("http://192.168.1.5:11434", "http://192.168.1.5:11434"),
    ("https://ollama.example.com", "https://ollama.example.com"),
    ("http://localhost.example.com:11434", "http://localhost.example.com:11434"),
    ("", "http://127.0.0.1:11434"),
    (None, "http://127.0.0.1:11434"),
])
def test_base_url_is_pinned_to_ipv4_loopback(given, expected):
    assert osup.ollama_base(given) == expected


def _cleaner_capturing_posts(base_url="http://localhost:11434"):
    cl = Cleaner({"provider": "ollama",
                  "ollama": {"base_url": base_url, "model": "m", "keep_alive": "30m"}})
    calls = []
    resp = MagicMock()
    resp.json.return_value = {"message": {"content": "Hello."}}

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return resp

    cl._session.post = post
    return cl, calls


def test_cleanup_calls_go_to_127_0_0_1():
    cl, calls = _cleaner_capturing_posts()
    cl._via_ollama("You clean text.", "hello")
    assert calls[0][0] == "http://127.0.0.1:11434/api/chat"


def test_warmup_goes_to_127_0_0_1_with_a_short_connect_timeout():
    cl, calls = _cleaner_capturing_posts()
    cl.warmup()
    url, kwargs = calls[0]
    assert url == "http://127.0.0.1:11434/api/chat"
    connect, _read = kwargs["timeout"]
    assert connect <= 1.0


def test_liveness_probe_goes_to_127_0_0_1(monkeypatch):
    seen = []
    monkeypatch.setattr(osup.requests, "get",
                        lambda url, timeout: seen.append(url) or MagicMock(status_code=200))
    osup.is_alive("http://localhost:11434")
    assert seen == ["http://127.0.0.1:11434/api/tags"]
