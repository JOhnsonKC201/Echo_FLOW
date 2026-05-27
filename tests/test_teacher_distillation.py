"""Teacher-model distillation layer tests.

The teacher loop re-cleans every dictation via a stronger LLM (Groq by
default) in the background, stores its output as source='teacher', and
feeds it to the PatternMiner. These tests cover the four moving parts:

1. `Cleaner.teach()` honors the teacher_enabled gate, returns the cloud
   model's cleanup, and swallows errors.
2. PatternMiner aggregates a teacher (raw, cleaned) pair just like any
   other source.
3. `Learner.recent_examples()` includes teacher pairs by default and
   excludes them when trust_teacher=False.
4. `Learner.personal_vocabulary()` honors trust_teacher symmetrically.
"""
from __future__ import annotations

import pytest


# ---------- Cleaner.teach() ---------------------------------------------------

def _stub_cleaner(monkeypatch, teacher_enabled: bool, groq_out: str | Exception = "POLISHED"):
    """Build a Cleaner with the teacher gate flipped and _via_groq stubbed."""
    from src.cleanup import Cleaner
    cfg = {
        "enabled": True,
        "provider": "ollama",
        "learning": {"teacher_enabled": teacher_enabled},
        "groq": {"model": "llama-3.3-70b-versatile"},
        "ollama": {"model": "qwen2.5:7b-instruct"},
    }
    c = Cleaner(cfg)

    def _fake_groq(self, system, text, *, max_tokens=None):
        if isinstance(groq_out, Exception):
            raise groq_out
        return groq_out

    monkeypatch.setattr(Cleaner, "_via_groq", _fake_groq)
    return c


def test_teach_returns_none_when_disabled(monkeypatch):
    c = _stub_cleaner(monkeypatch, teacher_enabled=False)
    assert c.teach("raw text") is None


def test_teach_returns_groq_output_when_enabled(monkeypatch):
    c = _stub_cleaner(monkeypatch, teacher_enabled=True, groq_out="Polished output.")
    assert c.teach("raw text") == "Polished output."


def test_teach_returns_none_on_groq_error(monkeypatch):
    err = RuntimeError("groq unreachable")
    c = _stub_cleaner(monkeypatch, teacher_enabled=True, groq_out=err)
    assert c.teach("raw text") is None


def test_teach_skips_prompt_style(monkeypatch):
    c = _stub_cleaner(monkeypatch, teacher_enabled=True, groq_out="X")
    assert c.teach("raw text", style="prompt") is None


def test_teach_returns_none_when_unchanged(monkeypatch):
    c = _stub_cleaner(monkeypatch, teacher_enabled=True, groq_out="raw text")
    assert c.teach("raw text") is None


def test_teach_drops_hallucinated_output(monkeypatch):
    # Hallucination guard returns True when output contains markdown signals.
    bad = "Cleaned Text: ** here ** is your audit"
    c = _stub_cleaner(monkeypatch, teacher_enabled=True, groq_out=bad)
    assert c.teach("raw") is None


# ---------- PatternMiner with teacher source ---------------------------------

def test_pattern_miner_aggregates_teacher_pair(tmp_path):
    from src.learn import PatternMiner
    db = str(tmp_path / "patterns.db")
    miner = PatternMiner(db)
    # 1↔1 token sub: "jonson" → "Johnson"
    miner.record("hello jonson there", "hello Johnson there")
    miner.record("call jonson now", "call Johnson now")
    patterns = miner.confident_patterns(min_confidence=0.7, min_total=2)
    assert patterns.get("jonson") == "Johnson"


# ---------- Learner trust_teacher gating -------------------------------------

def _seed(history, *, source: str, raw: str, cleaned: str, style: str = "casual"):
    history.log(window_title="x", style=style, language="en", duration_ms=100,
                raw_text=raw, cleaned_text=cleaned, source=source)


def test_recent_examples_includes_teacher_by_default(temp_db):
    from src.learn import Learner, LearningConfig
    h, db_path = temp_db
    _seed(h, source="desktop", raw="raw one two three", cleaned="Raw one two three.")
    _seed(h, source="teacher", raw="raw teacher four five", cleaned="Teacher four five.")
    learner = Learner(db_path, LearningConfig(enabled=True, max_examples=10, min_example_chars=3))
    raws = [r for r, _ in learner.recent_examples("casual", 10)]
    assert any("teacher" in r for r in raws)
    assert any("one two" in r for r in raws)


def test_recent_examples_excludes_teacher_when_distrusted(temp_db):
    from src.learn import Learner, LearningConfig
    h, db_path = temp_db
    _seed(h, source="desktop", raw="raw one two three", cleaned="Raw one two three.")
    _seed(h, source="teacher", raw="raw teacher four five", cleaned="Teacher four five.")
    learner = Learner(db_path, LearningConfig(enabled=True, max_examples=10,
                                              min_example_chars=3, trust_teacher=False))
    raws = [r for r, _ in learner.recent_examples("casual", 10)]
    assert not any("teacher" in r for r in raws)


def test_personal_vocabulary_includes_teacher_by_default(temp_db):
    from src.learn import Learner, LearningConfig, _vocab_cache  # noqa: F401
    import src.learn as learn_mod
    h, db_path = temp_db
    _seed(h, source="teacher", raw="x", cleaned="The Kubernetes pod started. Kubernetes works.")
    _seed(h, source="teacher", raw="y", cleaned="Kubernetes again. The Kubernetes cluster.")
    learner = Learner(db_path, LearningConfig(enabled=True, max_examples=10))
    learn_mod._vocab_cache = None  # bust 60s cache between tests
    vocab = learner.personal_vocabulary(10)
    assert "Kubernetes" in vocab


def test_personal_vocabulary_excludes_teacher_when_distrusted(temp_db):
    from src.learn import Learner, LearningConfig
    import src.learn as learn_mod
    h, db_path = temp_db
    _seed(h, source="teacher", raw="x", cleaned="The Kubernetes pod started. Kubernetes works.")
    _seed(h, source="teacher", raw="y", cleaned="Kubernetes again. The Kubernetes cluster.")
    learner = Learner(db_path, LearningConfig(enabled=True, max_examples=10, trust_teacher=False))
    learn_mod._vocab_cache = None
    vocab = learner.personal_vocabulary(10)
    assert "Kubernetes" not in vocab


# ---------- PatternMiner origin attribution ----------------------------------

def test_pattern_miner_attributes_user_vs_teacher_counts(tmp_path):
    """user_count and teacher_count track which source taught each pattern."""
    import sqlite3
    from src.learn import PatternMiner
    db = str(tmp_path / "pat.db")
    miner = PatternMiner(db)
    miner.record("hello jonson", "hello Johnson", source="user")
    miner.record("hi jonson", "hi Johnson", source="teacher")
    miner.record("call jonson", "call Johnson", source="teacher")
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT user_count, teacher_count FROM learned_patterns "
        "WHERE trigger='jonson' AND replacement='Johnson'"
    ).fetchone()
    assert row == (1, 2)


def test_pattern_miner_default_source_is_user(tmp_path):
    """Backward-compat: existing callers that omit source still count as user."""
    import sqlite3
    from src.learn import PatternMiner
    db = str(tmp_path / "pat.db")
    miner = PatternMiner(db)
    miner.record("hello jonson", "hello Johnson")  # no source kwarg
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT user_count, teacher_count FROM learned_patterns "
        "WHERE trigger='jonson'"
    ).fetchone()
    assert row == (1, 0)


# ---------- Anthropic provider routing ---------------------------------------

def test_anthropic_provider_is_routed_in_pe_mode(monkeypatch):
    """PE mode (provider_override set) allows anthropic; otherwise blocked."""
    from src.cleanup import Cleaner
    cfg = {
        "enabled": True, "provider": "ollama",
        "anthropic": {"model": "claude-haiku-4-5-20251001"},
        "ollama": {"model": "x"},
        "groq": {"model": "y"},
    }
    c = Cleaner(cfg)
    calls = {"anthropic": 0, "ollama": 0}
    monkeypatch.setattr(Cleaner, "_via_anthropic",
                        lambda self, system, text, max_tokens=None: (calls.__setitem__("anthropic", calls["anthropic"] + 1) or "ANT OUT"))
    monkeypatch.setattr(Cleaner, "_via_ollama",
                        lambda self, system, text, max_tokens=None, style="default": (calls.__setitem__("ollama", calls["ollama"] + 1) or "OLL OUT"))
    # In PE mode with explicit override, anthropic should be called.
    out, _ = c.clean("dictate this", style="prompt", provider_override="anthropic")
    assert calls["anthropic"] == 1
    assert "ANT" in out

