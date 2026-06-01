"""Cleaner.clean() fallback chain: primary fails → fallback runs; both fail → raw.

Covers src/cleanup.py:clean() ~lines 380-410. clean() returns (text, polish_skipped).
"""
from __future__ import annotations

import pytest


def _messy_text() -> str:
    # Filler-heavy so the skip-clean fast path doesn't short-circuit.
    return "um yeah like ship the thing you know"


def test_primary_fails_fallback_runs(monkeypatch):
    from src.cleanup import Cleaner
    cleaner = Cleaner({"enabled": True, "provider": "ollama",
                       "learned": {"fallback_to_ollama": False}})

    # Override _via_ollama (primary) to raise — provider_override="ollama" routes here.
    def _boom(*a, **k):
        raise RuntimeError("ollama down")
    monkeypatch.setattr(cleaner, "_via_ollama", _boom)

    # Stub _via_learned (fallback) to return a sentinel.
    def _learned(text, *, style="default"):
        return "FALLBACK_OK"
    monkeypatch.setattr(cleaner, "_via_learned", _learned)

    out, skipped = cleaner.clean(
        _messy_text(),
        provider_override="ollama",
        fallback_provider="learned",
    )
    # Successful cleanup output is now casing/punctuation-normalized (the skip
    # path always did this; it now also covers the provider path). The chain
    # still returns the fallback's content — modulo that final polish.
    assert out.rstrip(".") == "FALLBACK_OK"
    assert skipped is False


def test_both_fail_returns_raw(monkeypatch):
    from src.cleanup import Cleaner
    cleaner = Cleaner({"enabled": True, "provider": "ollama",
                       "learned": {"fallback_to_ollama": False}})

    def _boom_ollama(*a, **k):
        raise RuntimeError("ollama down")
    def _boom_learned(*a, **k):
        raise RuntimeError("learned down")

    monkeypatch.setattr(cleaner, "_via_ollama", _boom_ollama)
    monkeypatch.setattr(cleaner, "_via_learned", _boom_learned)

    raw = _messy_text()
    out, skipped = cleaner.clean(
        raw,
        provider_override="ollama",
        fallback_provider="learned",
    )
    assert out == raw
    assert skipped is False


def test_no_fallback_provider_returns_raw_on_primary_fail(monkeypatch):
    """When fallback_provider is None, primary failure must return raw immediately."""
    from src.cleanup import Cleaner
    cleaner = Cleaner({"enabled": True, "provider": "ollama"})

    def _boom(*a, **k):
        raise RuntimeError("nope")
    monkeypatch.setattr(cleaner, "_via_ollama", _boom)

    raw = _messy_text()
    out, skipped = cleaner.clean(raw, provider_override="ollama")
    assert out == raw
    assert skipped is False
