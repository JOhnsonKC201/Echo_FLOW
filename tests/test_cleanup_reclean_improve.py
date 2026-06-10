"""Cleaner.reclean_improve (second-pass verify-and-improve) and _via_anthropic.

Covers src/cleanup.py ~lines 996-1038 (reclean_improve) and 1040-1079
(_via_anthropic). All provider/HTTP calls are mocked — no network.

Contract notes (asserted below):
- reclean_improve returns the improved text on success, or None on any
  provider failure / hallucination-guard trip — it never raises; the CALLER
  keeps the prior text when None comes back.
- _via_anthropic raises RuntimeError when ANTHROPIC_API_KEY is missing and
  propagates HTTP errors; success parses Messages-API content blocks.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests


RAW = "um we ship the new feature yesterday and it work"
PRIOR = "We ship the new feature yesterday and it work."
IMPROVED = "We shipped the new feature yesterday and it works."


def _cleaner(extra: dict | None = None):
    from src.cleanup import Cleaner
    cfg = {"enabled": True, "provider": "ollama"}
    if extra:
        cfg.update(extra)
    return Cleaner(cfg)


# --- reclean_improve --------------------------------------------------------


def test_returns_improved_text_on_local_success(monkeypatch):
    cleaner = _cleaner()
    calls: list[tuple[str, str]] = []

    def _ollama(system, text, *, max_tokens=None, style="default"):
        calls.append((system, text))
        return IMPROVED

    monkeypatch.setattr(cleaner, "_via_ollama", _ollama)

    out = cleaner.reclean_improve(RAW, PRIOR)

    assert out is not None
    # Output passes through _finalize (casing/punctuation polish) — compare
    # words, not exact bytes, like the fallback-chain tests do.
    assert "shipped the new feature" in out.lower()
    # The second-pass prompt must carry both the original and the attempt.
    system, user = calls[0]
    assert "SECOND PASS" in system
    assert f"ORIGINAL DICTATION:\n{RAW}" in user
    assert f"FIRST ATTEMPT:\n{PRIOR}" in user


def test_provider_failure_returns_none_without_raising(monkeypatch):
    """Local pass blows up (timeout/conn refused) → None, never an exception.

    None tells the caller to keep the prior text — dictation never breaks."""
    cleaner = _cleaner()

    def _boom(*a, **k):
        raise requests.exceptions.Timeout("ollama timed out")

    monkeypatch.setattr(cleaner, "_via_ollama", _boom)

    assert cleaner.reclean_improve(RAW, PRIOR) is None


def test_cloud_failure_falls_back_to_local(monkeypatch):
    """use_cloud=True with a dead Groq (e.g. no key) must use Ollama instead."""
    cleaner = _cleaner()

    def _groq_boom(*a, **k):
        raise RuntimeError("GROQ_API_KEY env var is empty")

    monkeypatch.setattr(cleaner, "_via_groq", _groq_boom)
    monkeypatch.setattr(cleaner, "_via_ollama",
                        lambda *a, **k: IMPROVED)

    out = cleaner.reclean_improve(RAW, PRIOR, use_cloud=True)

    assert out is not None
    assert "shipped the new feature" in out.lower()


def test_cloud_and_local_both_fail_returns_none(monkeypatch):
    cleaner = _cleaner()

    def _boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(cleaner, "_via_groq", _boom)
    monkeypatch.setattr(cleaner, "_via_ollama", _boom)

    assert cleaner.reclean_improve(RAW, PRIOR, use_cloud=True) is None


def test_empty_inputs_return_none_without_calling_provider(monkeypatch):
    cleaner = _cleaner()

    def _never(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("provider must not be called for empty input")

    monkeypatch.setattr(cleaner, "_via_ollama", _never)

    assert cleaner.reclean_improve("", PRIOR) is None
    assert cleaner.reclean_improve(RAW, "") is None


def test_hallucinated_output_is_dropped(monkeypatch):
    """A chatbot-style structured response must be rejected → None."""
    cleaner = _cleaner()
    monkeypatch.setattr(
        cleaner, "_via_ollama",
        lambda *a, **k: "**Cleaned Text:**\n" + "blah " * 100,
    )

    assert cleaner.reclean_improve(RAW, PRIOR) is None


# --- _via_anthropic ----------------------------------------------------------


def _anthropic_response(blocks):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"content": blocks})
    return resp


def test_via_anthropic_request_shape_and_parsing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
    cleaner = _cleaner()
    resp = _anthropic_response([
        {"type": "text", "text": "Hello "},
        {"type": "tool_use", "id": "ignored"},   # non-text blocks skipped
        {"type": "text", "text": "world."},
    ])

    with patch.object(cleaner._session, "post", return_value=resp) as post:
        out = cleaner._via_anthropic("SYSTEM PROMPT", "user text")

    assert out == "Hello world."
    (url,), kwargs = post.call_args
    assert url == "https://api.anthropic.com/v1/messages"
    headers = kwargs["headers"]
    assert headers["x-api-key"] == "test-key-123"
    assert headers["anthropic-version"] == "2023-06-01"
    body = kwargs["json"]
    assert body["model"] == "claude-haiku-4-5-20251001"  # default model
    assert body["system"] == "SYSTEM PROMPT"
    assert body["max_tokens"] == 700                     # default cap
    assert body["messages"] == [{"role": "user", "content": "user text"}]


def test_via_anthropic_honors_config_and_max_tokens(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cleaner = _cleaner({"anthropic": {
        "base_url": "http://localhost:9999/v1/messages",
        "model": "claude-test-model",
        "timeout_sec": 5,
    }})
    resp = _anthropic_response([{"type": "text", "text": "ok"}])

    with patch.object(cleaner._session, "post", return_value=resp) as post:
        out = cleaner._via_anthropic("s", "t", max_tokens=200)

    assert out == "ok"
    (url,), kwargs = post.call_args
    assert url == "http://localhost:9999/v1/messages"
    assert kwargs["json"]["model"] == "claude-test-model"
    assert kwargs["json"]["max_tokens"] == 200
    assert kwargs["timeout"] == 5.0


def test_via_anthropic_missing_key_raises_before_any_request(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cleaner = _cleaner()

    with patch.object(cleaner._session, "post") as post:
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            cleaner._via_anthropic("s", "t")

    post.assert_not_called()  # the key check must short-circuit the network


def test_via_anthropic_http_error_propagates(monkeypatch):
    """4xx/5xx surfaces as an exception so callers' fallback chains engage."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cleaner = _cleaner()
    resp = MagicMock()
    resp.status_code = 500
    resp.raise_for_status = MagicMock(
        side_effect=requests.exceptions.HTTPError("500 Server Error"))

    with patch.object(cleaner._session, "post", return_value=resp):
        with pytest.raises(requests.exceptions.HTTPError):
            cleaner._via_anthropic("s", "t")
