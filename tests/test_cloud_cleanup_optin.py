"""cleanup.allow_cloud_cleanup — opt-in cloud cleanup for regular dictation.

By default Echo Flow keeps regular cleanup local (see test_cleanup_groq_gate.py).
When the user sets cleanup.allow_cloud_cleanup: true with provider=groq, regular
dictation cleanup goes to Groq — and falls back to local Ollama if Groq fails or
its key is missing, so dictation never breaks.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock


MESSY = "um yeah like ship the thing you know"


def _cleaner(allow_cloud):
    from src.cleanup import Cleaner
    return Cleaner({
        "enabled": True,
        "provider": "groq",
        "skip_when_clean": False,
        "allow_cloud_cleanup": allow_cloud,
        "groq": {"base_url": "https://api.groq.com/openai/v1/chat/completions"},
        "ollama": {"base_url": "http://localhost:11434"},
    })


def _ollama_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"message": {"content": "ollama cleaned."}})
    return resp


def _groq_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"choices": [{"message": {"content": "groq cleaned."}}]})
    return resp


def test_optin_routes_regular_cleanup_to_groq(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "valid-key")
    cleaner = _cleaner(allow_cloud=True)
    urls: list[str] = []

    def _fake_post(url, *a, **k):
        urls.append(url)
        return _groq_response() if "groq.com" in url else _ollama_response()

    with patch.object(cleaner._session, "post", side_effect=_fake_post):
        out, _ = cleaner.clean(MESSY, style="default")

    assert any("groq.com" in u for u in urls), f"expected a Groq call, got {urls}"
    assert "groq cleaned" in out.lower()


def test_optin_off_keeps_regular_cleanup_local(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "should-not-be-used")
    cleaner = _cleaner(allow_cloud=False)
    urls: list[str] = []

    def _fake_post(url, *a, **k):
        urls.append(url)
        assert "groq.com" not in url, "Groq must not be hit when opt-in is off!"
        return _ollama_response()

    with patch.object(cleaner._session, "post", side_effect=_fake_post):
        out, _ = cleaner.clean(MESSY, style="default")
    assert out and all("groq.com" not in u for u in urls)


def test_optin_falls_back_to_ollama_when_groq_key_missing(monkeypatch):
    """No GROQ_API_KEY → _via_groq raises → regular cleanup must fall back to
    Ollama (dictation never breaks), NOT raw-passthrough."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    cleaner = _cleaner(allow_cloud=True)
    urls: list[str] = []

    def _fake_post(url, *a, **k):
        urls.append(url)
        return _ollama_response()  # groq never reaches POST (key check raises first)

    with patch.object(cleaner._session, "post", side_effect=_fake_post):
        out, skipped = cleaner.clean(MESSY, style="default")

    assert "ollama cleaned" in out.lower(), f"expected Ollama fallback, got {out!r}"
    assert any("11434" in u or "api/chat" in u for u in urls)
    assert skipped is False
