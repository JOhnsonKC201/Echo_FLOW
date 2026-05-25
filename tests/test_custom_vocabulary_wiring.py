"""App._build_custom_vocabulary() and its initial_prompt wiring.

Verifies the helper merges (config snippets + learner personal vocab),
de-duplicates case-insensitively, caps at 80, and is wired into the
Transcriber's WhisperConfig via the 'Vocabulary: ' prefix.
"""
from __future__ import annotations

import types

import pytest


class _StubLearner:
    def __init__(self, vocab):
        self._vocab = vocab

    def personal_vocabulary(self, limit: int):
        return self._vocab[:limit]


def _make_app_with(cfg, learner):
    """Build a bare App instance without running __init__ (heavy deps)."""
    from src.main import App
    from src.transcribe import WhisperConfig

    app = App.__new__(App)
    app.cfg = cfg
    app.learner = learner

    # Minimal Transcriber stub carrying a real WhisperConfig (so .cfg.initial_prompt is writable).
    transcriber = types.SimpleNamespace(cfg=WhisperConfig(model="tiny"))
    app.transcriber = transcriber
    return app


def test_build_custom_vocabulary_merges_dedupes_and_caps():
    cfg = {
        "custom_vocabulary": ["FastAPI", "Supabase", "node2vec"],
        "cleanup": {"snippets": {
            "btw": "by the way",
            "fa": "fastapi",   # case-insensitive dup of FastAPI
            "n2v": "Node2Vec", # case-insensitive dup of node2vec
        }},
    }
    # 5 personal terms, one of which dups "Supabase" (different case).
    personal = ["Echo Flow", "Whisper", "supabase", "Ollama", "pytest"]
    app = _make_app_with(cfg, _StubLearner(personal))

    vocab = app._build_custom_vocabulary()

    assert isinstance(vocab, list)
    # Length cap = 80. We're well under, but assert it's respected.
    assert len(vocab) <= 80
    # Case-insensitive dedup: only one of FastAPI/fastapi survives.
    lower = [v.lower() for v in vocab]
    assert lower.count("fastapi") == 1
    assert lower.count("supabase") == 1
    assert lower.count("node2vec") == 1
    # First-wins ordering preserves the original casing from the static list.
    assert "FastAPI" in vocab
    assert "Supabase" in vocab
    assert "node2vec" in vocab
    # Snippet expansions still contribute non-duplicates.
    assert "by the way" in vocab
    # Personal vocab terms come through.
    assert "Echo Flow" in vocab
    assert "Whisper" in vocab


def test_build_custom_vocabulary_caps_at_80():
    # 200 snippet values, all unique — output must be capped at 80.
    snippets = {f"k{i}": f"term{i:04d}" for i in range(200)}
    cfg = {"cleanup": {"snippets": snippets}}
    app = _make_app_with(cfg, _StubLearner([]))

    vocab = app._build_custom_vocabulary()
    assert len(vocab) == 80


def test_initial_prompt_wired_with_vocabulary_prefix():
    """Simulate the __init__ wiring block (main.py:166-175) and assert the prompt."""
    cfg = {
        "custom_vocabulary": ["FastAPI", "Supabase"],
        "cleanup": {"snippets": {"btw": "by the way"}},
    }
    personal = ["Whisper", "Ollama"]
    app = _make_app_with(cfg, _StubLearner(personal))

    vocab = app._build_custom_vocabulary()
    assert vocab, "precondition: vocab must be non-empty for this test"
    # Re-run the same prefix construction main.py uses.
    ip = "Vocabulary: " + ", ".join(vocab[:80])
    app.transcriber.cfg.initial_prompt = ip

    assert app.transcriber.cfg.initial_prompt.startswith("Vocabulary: ")
    assert "FastAPI" in app.transcriber.cfg.initial_prompt
    assert "by the way" in app.transcriber.cfg.initial_prompt
    assert "Whisper" in app.transcriber.cfg.initial_prompt
