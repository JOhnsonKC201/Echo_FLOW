"""Error-path coverage for Cleaner._via_ollama via Cleaner.clean().

For each failure mode (timeout, HTTP 500, malformed/missing JSON keys), clean()
must return the user's WORDS (never reworded) with polish_skipped=False, and
notify.notify must be called exactly once at "error" severity. The LLM-free
casing/punctuation pass still runs on the passthrough, so a provider outage
can't surface Whisper's "Every Word Capitalized" output unflattened.
"""
from __future__ import annotations

import pytest
import requests


RAW = "um yeah like ship the thing you know"


def _make_cleaner():
    from src.cleanup import Cleaner
    return Cleaner({"enabled": True, "provider": "ollama",
                    "ollama": {"model": "test-model", "timeout_sec": 1.0}})


class _Resp500:
    status_code = 500
    def raise_for_status(self):
        raise requests.HTTPError("500 server error")
    def json(self):
        return {"message": {"content": "should not be read"}}


class _RespMalformedNoMessage:
    status_code = 200
    def raise_for_status(self): pass
    def json(self):
        return {"unexpected": "shape"}


class _RespMissingContent:
    status_code = 200
    def raise_for_status(self): pass
    def json(self):
        return {"message": {"role": "assistant"}}  # no "content"


def _timeout_post(*a, **k):
    raise requests.Timeout("simulated timeout")


def _http500_post(*a, **k):
    return _Resp500()


def _malformed_post(*a, **k):
    return _RespMalformedNoMessage()


def _missing_content_post(*a, **k):
    return _RespMissingContent()


@pytest.mark.parametrize("fake_post", [
    _timeout_post,
    _http500_post,
    _malformed_post,
    _missing_content_post,
], ids=["timeout", "http500", "malformed_json", "missing_content"])
def test_via_ollama_error_returns_raw_and_notifies(monkeypatch, fake_post):
    from src import cleanup as cleanup_mod
    cleaner = _make_cleaner()

    monkeypatch.setattr(cleaner._session, "post", fake_post)

    notify_calls: list[tuple] = []
    def _fake_notify(title, message, level="info"):
        notify_calls.append((title, message, level))
    monkeypatch.setattr(cleanup_mod.notify, "notify", _fake_notify)

    out, skipped = cleaner.clean(RAW)
    # Words preserved (no rewording), casing/punctuation normalized.
    assert out.rstrip(".").lower() == RAW, f"words must be preserved on error; got {out!r}"
    assert out[0].isupper()
    assert skipped is False
    assert len(notify_calls) == 1, f"expected one error notify; got {notify_calls}"
    assert notify_calls[0][2] == "error"
