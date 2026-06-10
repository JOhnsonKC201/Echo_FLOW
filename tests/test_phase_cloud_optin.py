"""Phase decisions honor the cloud-cleanup opt-in.

Before this, phase.decide() silently normalized cleanup.provider=groq away
(only ever returning ollama/learned/none), so a user who set
allow_cloud_cleanup + GROQ never actually got Groq cleanup — and with Ollama
down they got provider "none": raw Whisper output with no casing or
punctuation pass at all. That single path produced 75% unpolished dictations.
"""
from __future__ import annotations

from src import phase as phase_mod

# Port 1 is never listening — Ollama reads as down in every test here.
_DEAD_OLLAMA = {"base_url": "http://localhost:1"}


def _cfg(provider: str, *, allow_cloud: bool, phasing_enabled: bool = True):
    return {
        "phasing": {"enabled": phasing_enabled, "self_sufficient_after": 2000},
        "whisper": {"backend": "local"},
        "cleanup": {
            "provider": provider,
            "allow_cloud_cleanup": allow_cloud,
            "ollama": _DEAD_OLLAMA,
        },
    }


def test_cloud_optin_returns_groq_even_with_ollama_down(temp_db):
    """Explicit opt-in (allow_cloud_cleanup + groq) is honored: Cleaner.clean()
    owns the groq→ollama→finalized-raw fallback, so phasing must not preempt it."""
    _, path = temp_db
    d = phase_mod.decide(_cfg("groq", allow_cloud=True), path)
    assert d.cleanup_provider == "groq"
    assert d.transcribe_backend == "local"
    assert d.degraded is False


def test_cloud_optin_anthropic(temp_db):
    _, path = temp_db
    d = phase_mod.decide(_cfg("anthropic", allow_cloud=True), path)
    assert d.cleanup_provider == "anthropic"


def test_cloud_provider_without_optin_falls_back_locally(temp_db):
    """groq named but allow_cloud_cleanup off → local pipeline; with Ollama
    down that means the degraded learned/deterministic provider, not 'none'."""
    _, path = temp_db
    d = phase_mod.decide(_cfg("groq", allow_cloud=False), path)
    assert d.cleanup_provider == "learned"
    assert d.degraded is True


def test_no_optin_ollama_down_degrades_to_learned_not_none(temp_db):
    """The old behavior returned 'none' (true raw passthrough — no casing
    flatten, no punctuation). It must degrade to 'learned', whose empty-data
    path is a deterministic polish."""
    _, path = temp_db
    d = phase_mod.decide(_cfg("ollama", allow_cloud=False), path)
    assert d.cleanup_provider == "learned"
    assert d.degraded is True
    assert "none" not in (d.cleanup_provider,)


def test_phasing_disabled_honors_cloud_optin(temp_db):
    """phasing.enabled=false used to normalize groq→ollama unconditionally;
    with the explicit opt-in the user's provider choice stands."""
    _, path = temp_db
    d = phase_mod.decide(_cfg("groq", allow_cloud=True, phasing_enabled=False), path)
    assert d.name == "manual"
    assert d.cleanup_provider == "groq"


def test_phasing_disabled_without_optin_still_normalizes(temp_db):
    """Without the opt-in, the legacy normalization (cloud name → ollama)
    is preserved for phasing-disabled configs."""
    _, path = temp_db
    d = phase_mod.decide(_cfg("groq", allow_cloud=False, phasing_enabled=False), path)
    assert d.name == "manual"
    assert d.cleanup_provider == "ollama"
