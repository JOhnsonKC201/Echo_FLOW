"""Groq carve-out gate — Echo Flow is local-only EXCEPT in Prompt-Engineering mode.

Regression guard for src/cleanup.py:_run_provider. The carve-out is enabled iff
BOTH conditions hold:
    style == "prompt"  AND  provider_override is not None
Any other configuration that names 'groq' must be rerouted to ollama and the
Groq endpoint must NEVER be contacted (which would leak audio-derived text to
the cloud).
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


# Filler-heavy so the "already clean" fast-path doesn't short-circuit before
# _run_provider is even reached.
MESSY = "um yeah like ship the thing you know"


def _cleaner(provider="ollama"):
    from src.cleanup import Cleaner
    return Cleaner({
        "enabled": True,
        "provider": provider,
        "skip_when_clean": False,  # belt + suspenders: force LLM path
        "groq": {"base_url": "https://api.groq.com/openai/v1/chat/completions"},
        "ollama": {"base_url": "http://localhost:11434"},
    })


def _ollama_response():
    """Build a fake `requests` response object that looks like Ollama's reply."""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"message": {"content": "cleaned text."}})
    return resp


def _groq_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={
        "choices": [{"message": {"content": "groq-cleaned"}}]
    })
    return resp


def test_default_style_routes_groq_to_ollama(monkeypatch):
    """provider="groq", style="default", no override → MUST go to ollama only."""
    monkeypatch.setenv("GROQ_API_KEY", "should-not-be-used")
    cleaner = _cleaner(provider="groq")
    posts: list[str] = []

    def _fake_post(url, *a, **k):
        posts.append(url)
        assert "groq.com" not in url, "GROQ URL leaked in default style!"
        return _ollama_response()

    with patch.object(cleaner._session, "post", side_effect=_fake_post):
        out, _ = cleaner.clean(MESSY, style="default")

    assert out  # non-empty
    assert posts, "no provider call happened at all"
    assert all("groq.com" not in u for u in posts)
    assert any("11434" in u or "ollama" in u or "api/chat" in u for u in posts)


def test_prompt_style_without_override_routes_to_ollama(monkeypatch):
    """style='prompt' alone is NOT enough — provider_override must also be set."""
    monkeypatch.setenv("GROQ_API_KEY", "should-not-be-used")
    cleaner = _cleaner(provider="groq")

    with patch.object(cleaner._session, "post",
                      side_effect=lambda url, *a, **k: (
                          # If Groq is hit here, the safety gate broke.
                          (_ for _ in ()).throw(AssertionError(f"GROQ hit: {url}"))
                          if "groq.com" in url else _ollama_response()
                      )):
        out, _ = cleaner.clean(MESSY, style="prompt")  # no provider_override
    assert out


def test_default_style_with_groq_override_still_routes_to_ollama(monkeypatch):
    """provider_override='groq' alone is NOT enough — style must also be 'prompt'."""
    monkeypatch.setenv("GROQ_API_KEY", "should-not-be-used")
    cleaner = _cleaner(provider="ollama")

    with patch.object(cleaner._session, "post",
                      side_effect=lambda url, *a, **k: (
                          (_ for _ in ()).throw(AssertionError(f"GROQ hit: {url}"))
                          if "groq.com" in url else _ollama_response()
                      )):
        out, _ = cleaner.clean(MESSY, style="default", provider_override="groq")
    assert out


def test_prompt_style_plus_override_actually_hits_groq(monkeypatch):
    """The single allowed combo: style='prompt' AND provider_override='groq'."""
    monkeypatch.setenv("GROQ_API_KEY", "valid-key")
    cleaner = _cleaner(provider="ollama")
    hit_urls: list[str] = []

    def _fake_post(url, *a, **k):
        hit_urls.append(url)
        if "groq.com" in url:
            return _groq_response()
        return _ollama_response()

    with patch.object(cleaner._session, "post", side_effect=_fake_post):
        out, _ = cleaner.clean(MESSY, style="prompt", provider_override="groq")

    assert any("groq.com" in u for u in hit_urls), \
        f"Expected at least one Groq call when carve-out is armed, got: {hit_urls}"
    assert out == "groq-cleaned"


def test_groq_with_empty_api_key_raises(monkeypatch):
    """Even in the carve-out, an empty GROQ_API_KEY must abort — not silently leak."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    cleaner = _cleaner(provider="ollama")

    # _via_groq raises RuntimeError; clean() catches via the outer try/except
    # and returns raw text (no fallback was provided).
    with patch.object(cleaner._session, "post", return_value=_groq_response()):
        out, skipped = cleaner.clean(MESSY, style="prompt", provider_override="groq")
    assert out == MESSY  # raw fallback path
    assert skipped is False
