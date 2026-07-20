"""Cleaner.humanize — the "My Voice" light-touch rewrite pass.

All provider calls are mocked (no network). Contract:
- returns the humanized text on success, or None on any provider failure,
  guard trip, or meaning-drift — never raises; the CALLER keeps `cleaned`.
- the prompt carries the humanize directive + the voice profile as data.
"""
from __future__ import annotations

import numpy as np
import requests


VOICE = "WRITING SAMPLES (how you actually write):\nShort. Punchy. To the point."
CLEANED = "We shipped the new feature yesterday and it works well."
HUMANIZED = "Shipped the new feature yesterday. Works well."


def _cleaner():
    from src.cleanup import Cleaner
    return Cleaner({"enabled": True, "provider": "ollama"})


class _FakeRetriever:
    """embed_text → unit vectors so np.dot == cosine. Output containing
    'DIVERGE' embeds orthogonally to the cleaned text (forces low similarity)."""
    def embed_text(self, t):
        return np.array([0.0, 1.0]) if "DIVERGE" in t else np.array([1.0, 0.0])


def test_returns_humanized_on_local_success(monkeypatch):
    cleaner = _cleaner()
    calls = []
    monkeypatch.setattr(cleaner, "_via_ollama",
                        lambda system, text, *a, **k: calls.append((system, text)) or HUMANIZED)

    out = cleaner.humanize(CLEANED, voice_profile=VOICE, retriever=_FakeRetriever())

    assert out is not None
    assert "shipped the new feature" in out.lower()
    system, user = calls[0]
    assert "VOICE PROFILE (style reference only" in system
    assert "how you actually write" in system          # profile embedded as data
    assert user == CLEANED                              # rewrites the cleaned text


def test_empty_cleaned_or_profile_returns_none_without_calling_provider(monkeypatch):
    cleaner = _cleaner()
    called = []
    monkeypatch.setattr(cleaner, "_via_ollama",
                        lambda *a, **k: called.append(1) or HUMANIZED)
    assert cleaner.humanize("", voice_profile=VOICE) is None
    assert cleaner.humanize(CLEANED, voice_profile="") is None
    assert called == []                                 # short-circuits before any LLM


def test_provider_failure_returns_none(monkeypatch):
    cleaner = _cleaner()

    def _boom(*a, **k):
        raise requests.exceptions.Timeout("ollama down")

    monkeypatch.setattr(cleaner, "_via_ollama", _boom)
    assert cleaner.humanize(CLEANED, voice_profile=VOICE) is None


def test_cloud_falls_back_to_local(monkeypatch):
    cleaner = _cleaner()
    monkeypatch.setattr(cleaner, "_via_groq",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no key")))
    monkeypatch.setattr(cleaner, "_via_ollama", lambda *a, **k: HUMANIZED)

    out = cleaner.humanize(CLEANED, voice_profile=VOICE, use_cloud=True,
                           retriever=_FakeRetriever())
    assert out is not None and "shipped" in out.lower()


def test_markdown_output_dropped_by_guard(monkeypatch):
    cleaner = _cleaner()
    monkeypatch.setattr(cleaner, "_via_ollama",
                        lambda *a, **k: "**Rewritten:**\n- shipped the feature\n- it works")
    assert cleaner.humanize(CLEANED, voice_profile=VOICE) is None


def test_oversize_output_dropped(monkeypatch):
    cleaner = _cleaner()
    monkeypatch.setattr(cleaner, "_via_ollama",
                        lambda *a, **k: "shipped " * 200)   # way over 1.6x
    assert cleaner.humanize(CLEANED, voice_profile=VOICE) is None


def test_meaning_drift_rejected(monkeypatch):
    cleaner = _cleaner()
    # A short output (passes length guard) but semantically orthogonal → rejected.
    monkeypatch.setattr(cleaner, "_via_ollama",
                        lambda *a, **k: "DIVERGE totally different meaning here now.")
    out = cleaner.humanize(CLEANED, voice_profile=VOICE,
                           retriever=_FakeRetriever(), min_sim=0.85)
    assert out is None


def test_on_meaning_output_accepted(monkeypatch):
    cleaner = _cleaner()
    monkeypatch.setattr(cleaner, "_via_ollama", lambda *a, **k: HUMANIZED)
    out = cleaner.humanize(CLEANED, voice_profile=VOICE,
                           retriever=_FakeRetriever(), min_sim=0.85)
    assert out is not None and "works well" in out.lower()


def test_no_embedder_uses_lenient_lexical_floor(monkeypatch):
    cleaner = _cleaner()
    # No retriever → lexical fallback. A light edit keeps enough tokens → accepted.
    monkeypatch.setattr(cleaner, "_via_ollama", lambda *a, **k: HUMANIZED)
    assert cleaner.humanize(CLEANED, voice_profile=VOICE, retriever=None) is not None
    # A wholesale replacement shares almost no tokens → rejected.
    monkeypatch.setattr(cleaner, "_via_ollama",
                        lambda *a, **k: "Completely unrelated sentence about zebras.")
    assert cleaner.humanize(CLEANED, voice_profile=VOICE, retriever=None) is None
