"""Casing on the RAW-passthrough escape paths.

The normal clean() path runs `_finalize`, which de-Title-Cases spurious
"Every Word Capitalized" Whisper output. But two paths bypass `_finalize` and
paste the user's RAW words verbatim:

  1. the hallucination guard (model went off-track), and
  2. total cleanup failure (every provider raised).

When Whisper itself produced Title-Cased text, those paths used to surface it
unflattened — the "sometimes capitalized, sometimes mirrored" bug. Casing
flattening is content-preserving (it changes capitalization, never words), so it
must still run on passthrough. These tests pin that.
"""
from __future__ import annotations

from src.history import History
from src.learn import PatternMiner, _invalidate_casing_cache


def _cleaner(tmp_path):
    from src.cleanup import Cleaner

    db = str(tmp_path / "h.db")
    History(db)  # create schema
    pm = PatternMiner(db)
    _invalidate_casing_cache()
    cleaner = Cleaner({
        "enabled": True, "provider": "ollama",
        "casing": {"flatten_titlecase": True, "learn_from_edits": True,
                   "protect_common_nouns": True},
        # Keep the teacher fallback off so a provider failure lands on the
        # raw-passthrough path we're exercising, not the teacher.
        "learning": {"teacher_enabled": False},
    })
    cleaner.attach_learning(pm, None)
    return cleaner


# The exact failure mode from the bug report: Whisper emitted every word
# Title-Cased ("a"/"I" aside). Kept >140 chars and free of trailing-only
# punctuation cues so it does NOT hit the skip-when-clean fast path (which
# already finalizes) — these tests must reach the LLM-then-guard path.
RAW_TITLECASED = (
    "Create a Skill All Your Explanation Currently Is To Bulky And Most Of "
    "Them Are Beyond The Things That I Asked So Can We Create A Skill That "
    "Gives More Context On The Quiz"
)
assert len(RAW_TITLECASED) > 140  # guard: must bypass the already-clean fast path


def test_hallucination_guard_passthrough_flattens_casing(tmp_path, monkeypatch):
    cleaner = _cleaner(tmp_path)
    # Model returns a structured/markdown chatbot answer → the hallucination
    # guard trips → the user's RAW words are pasted instead.
    monkeypatch.setattr(
        cleaner, "_via_ollama",
        lambda system, text, **kw: "**Cleaned Text:** here is a totally "
        "different and much longer rewrite that is clearly off the rails",
    )

    out, _skipped = cleaner.clean(RAW_TITLECASED)

    # The words are preserved (passthrough), but the spurious Title-Case is gone.
    assert "Create a skill all your explanation" in out
    assert "Your" not in out and "Bulky" not in out and "Things" not in out
    assert "I" in out.split()  # standalone "I" stays upper


def test_provider_failure_passthrough_flattens_casing(tmp_path, monkeypatch):
    cleaner = _cleaner(tmp_path)

    def _boom(*a, **k):
        raise RuntimeError("ollama is down")

    monkeypatch.setattr(cleaner, "_via_ollama", _boom)

    out, _skipped = cleaner.clean(RAW_TITLECASED)

    assert "Create a skill all your explanation" in out
    assert "Your" not in out and "Bulky" not in out and "Things" not in out


def test_learned_provider_no_fallback_flattens_casing(tmp_path, monkeypatch):
    """provider=learned with fallback_to_ollama=False used to return raw text
    (no _finalize), so Whisper Title-Case survived. It must flatten now."""
    cleaner = _cleaner(tmp_path)
    cleaner.provider = "learned"
    cleaner.cfg["learned"] = {"fallback_to_ollama": False}
    # No high-confidence learned fix → _via_learned returns None → the no-fallback
    # branch returns the user's words, casing-normalized.
    monkeypatch.setattr(cleaner, "_via_learned", lambda text, *, style="default": None)

    out, _skipped = cleaner.clean(RAW_TITLECASED)

    assert "Create a skill all your explanation" in out
    assert "Your" not in out and "Bulky" not in out


def test_phase_degraded_chain_flattens_casing(tmp_path, monkeypatch):
    """The full degraded path: Ollama down → phase.decide() picks 'learned'
    (no longer 'none') → learned has no data → ollama fallback raises →
    the deterministic polish still flattens Whisper Title-Case. This is the
    exact chain that used to paste 'Write Me A Reply' verbatim."""
    cleaner = _cleaner(tmp_path)
    cleaner.provider = "learned"  # what phase.decide() now applies when degraded
    cleaner.cfg["learned"] = {"fallback_to_ollama": True}

    def _boom(*a, **k):
        raise RuntimeError("ollama is down")

    monkeypatch.setattr(cleaner, "_via_ollama", _boom)

    out, _skipped = cleaner.clean(RAW_TITLECASED)

    assert "Create a skill all your explanation" in out
    assert "Your" not in out and "Bulky" not in out and "Things" not in out
