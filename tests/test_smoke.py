"""Smoke tests — lock down current correct behavior so refactors are safe."""
import sqlite3

import numpy as np
import pytest


# --- Hallucination guard ------------------------------------------------------

def test_hallucination_guard_normal_passes():
    from src.cleanup import Cleaner
    raw = "hi how you"
    out = "Hi, how are you?"
    assert Cleaner._looks_hallucinated(raw, out) is False


def test_hallucination_guard_long_output_trips():
    from src.cleanup import Cleaner
    # Guard threshold: max(80, len(raw)*2.5). Pick raw that makes threshold ~50,
    # then make output way bigger than that.
    raw = "what time meeting today and tomorrow"  # 35 chars -> threshold ~87
    out = "The meeting is at 3pm today. " * 8        # 232 chars
    assert Cleaner._looks_hallucinated(raw, out) is True


def test_hallucination_guard_markdown_trips():
    from src.cleanup import Cleaner
    raw = "what time meeting"
    out = "**Meeting Time:** 3pm tomorrow."
    assert Cleaner._looks_hallucinated(raw, out) is True


def test_hallucination_guard_audit_signature_trips():
    from src.cleanup import Cleaner
    raw = "hi"
    out = "Cleaned Text: Hi"
    assert Cleaner._looks_hallucinated(raw, out) is True


# --- Skip-polish heuristic ---------------------------------------------------

def test_skip_polish_clean_sentence():
    from src.cleanup import Cleaner
    assert Cleaner._is_already_clean("Let's ship the migration tonight.") is True


def test_skip_polish_filler_blocks():
    from src.cleanup import Cleaner
    assert Cleaner._is_already_clean("Um, let's ship it.") is False
    assert Cleaner._is_already_clean("You know, ship it now.") is False


def test_skip_polish_missing_punctuation_blocks():
    from src.cleanup import Cleaner
    assert Cleaner._is_already_clean("Let's ship the migration tonight") is False


def test_skip_polish_lowercase_start_blocks():
    from src.cleanup import Cleaner
    assert Cleaner._is_already_clean("let's ship it tonight.") is False


def test_skip_polish_repeat_blocks():
    from src.cleanup import Cleaner
    assert Cleaner._is_already_clean("Ship the the migration.") is False


def test_skip_polish_long_blocks():
    from src.cleanup import Cleaner
    long = ("A " * 80) + "."
    assert Cleaner._is_already_clean(long) is False


def test_clean_skips_llm_on_clean_input(monkeypatch):
    """End-to-end: clean() short-circuits without hitting Ollama."""
    from src.cleanup import Cleaner
    cleaner = Cleaner({"enabled": True, "provider": "ollama"})

    def _boom(*a, **k):
        raise AssertionError("LLM was called on already-clean input")
    monkeypatch.setattr(cleaner, "_via_ollama", _boom)

    out = cleaner.clean("Let's ship the migration tonight.")
    assert out == "Let's ship the migration tonight."


def test_clean_calls_llm_on_messy_input(monkeypatch):
    from src.cleanup import Cleaner
    cleaner = Cleaner({"enabled": True, "provider": "ollama"})
    called = []

    def _fake(prompt, text):
        called.append(text)
        return "Cleaned output."
    monkeypatch.setattr(cleaner, "_via_ollama", _fake)

    out = cleaner.clean("um yeah like ship the thing")
    assert called, "LLM should have been called on filler-heavy input"
    assert out == "Cleaned output."


def test_skip_path_runs_even_with_augmentation(monkeypatch):
    """Clean input with augmentation should STILL skip the LLM."""
    from src.cleanup import Cleaner
    cleaner = Cleaner({"enabled": True, "provider": "ollama"})

    def _boom(*a, **k):
        raise AssertionError("LLM was called despite clean input")
    monkeypatch.setattr(cleaner, "_via_ollama", _boom)

    out = cleaner.clean("Let's ship the migration tonight.",
                        augmentation="\nFew-shot examples:\n...")
    assert out == "Let's ship the migration tonight."


# --- Hallucination guard: short-input floor ----------------------------------

def test_hallucination_short_input_strict_floor():
    """For raw='hi' (2 chars), an 80-char output must be flagged."""
    from src.cleanup import Cleaner
    raw = "hi"
    out = "Hi there how are you doing today I hope you are well thanks for asking."
    assert Cleaner._looks_hallucinated(raw, out) is True


# --- Ollama call uses session + timeout + num_predict ------------------------

def test_via_ollama_passes_num_predict(monkeypatch):
    from src.cleanup import Cleaner
    cleaner = Cleaner({
        "enabled": True, "provider": "ollama",
        "ollama": {"model": "test-model", "timeout_sec": 5.0},
    })
    cleaner._max_tokens_override = 700
    captured = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": "ok"}}

    def _fake_post(url, json=None, timeout=None, **k):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(cleaner._session, "post", _fake_post)
    out = cleaner._via_ollama("sys", "user")
    assert out == "ok"
    assert captured["timeout"] == 5.0
    assert captured["json"]["options"]["num_predict"] == 700
    assert captured["json"]["keep_alive"] == "10m"


# --- Learned provider: case-preserving substitution --------------------------

def test_learned_pattern_preserves_case():
    """Capitalized input token stays capitalized after substitution."""
    from src.cleanup import Cleaner

    class _FakeMiner:
        def confident_patterns(self, min_confidence): return {"hi": "hello"}

    cleaner = Cleaner({
        "enabled": True, "provider": "learned",
        "learned": {"min_similarity": 0.99, "fallback_to_ollama": False},
    })
    cleaner.attach_learning(_FakeMiner(), None)
    out = cleaner._via_learned("Hi friend")
    assert out is not None
    # "Hi" should become "Hello" (capitalized), not "hello".
    assert "Hello" in out
    assert "hello friend" not in out.lower() or out.startswith("Hello")


# --- Embeddings round-trip ----------------------------------------------------

def test_embedding_blob_roundtrip():
    from src.retrieval import to_blob, from_blob
    vec = np.random.RandomState(42).rand(384).astype(np.float32)
    vec = vec / np.linalg.norm(vec)
    blob = to_blob(vec)
    restored = from_blob(blob)
    assert restored.shape == (384,)
    assert restored.dtype == np.float32
    np.testing.assert_allclose(vec, restored, rtol=1e-6)


# --- History --------------------------------------------------------------

def test_history_log_returns_id(temp_db):
    h, _ = temp_db
    rid = h.log(
        window_title="t", style="default", language="en",
        duration_ms=1000, raw_text="hi", cleaned_text="Hi.",
    )
    assert isinstance(rid, int) and rid > 0


def test_history_schema_migration_idempotent(temp_db):
    from src.history import History
    h, path = temp_db
    # Reopen — migration must not error on existing schema
    h2 = History(path)
    cols = [r[1] for r in h2.conn.execute("PRAGMA table_info(dictations)").fetchall()]
    assert "embedding" in cols
    assert "raw_text" in cols
    assert "cleaned_text" in cols


def test_history_log_embedding_blob(temp_db):
    h, _ = temp_db
    blob = b"\x00" * 8
    rid = h.log(
        window_title="t", style="default", language="en",
        duration_ms=1, raw_text="x", cleaned_text="X", embedding=blob,
    )
    row = h.conn.execute(
        "SELECT embedding FROM dictations WHERE id=?", (rid,)
    ).fetchone()
    assert row[0] == blob


# --- Phase decisions ----------------------------------------------------------

def test_phase_decision_independent_when_no_history(temp_db):
    """Local-only: a fresh install with Ollama unreachable falls back to raw output,
    transcribe backend is always local."""
    from src import phase as phase_mod
    _, path = temp_db
    cfg = {
        "phasing": {"enabled": True, "self_sufficient_after": 2000},
        "whisper": {"backend": "local"},
        "cleanup": {"provider": "ollama",
                    "ollama": {"base_url": "http://localhost:1"}},
    }
    decision = phase_mod.decide(cfg, path)
    assert decision.name == "independent"
    assert decision.transcribe_backend == "local"
    # Port 1 is dead → ollama_alive is False → cleanup_provider == "none"
    assert decision.cleanup_provider == "none"


def test_phase_respects_disabled_flag(temp_db):
    """When phasing is disabled, transcribe backend is still forced to local,
    and a legacy cloud provider name is normalized to ollama."""
    from src import phase as phase_mod
    _, path = temp_db
    cfg = {
        "phasing": {"enabled": False},
        "whisper": {"backend": "local"},
        "cleanup": {"provider": "groq",
                    "ollama": {"base_url": "http://localhost:1"}},
    }
    decision = phase_mod.decide(cfg, path)
    assert decision.name == "manual"
    assert decision.transcribe_backend == "local"
    assert decision.cleanup_provider == "ollama"


# --- Singleton lock -----------------------------------------------------------

def test_singleton_blocks_second(monkeypatch):
    """Second acquire on same port must call sys.exit."""
    import socket
    from src import singleton

    # Use an isolated port so we don't collide with a real running daemon
    test_port = 53219
    monkeypatch.setattr(singleton, "_LOCK_PORT", test_port)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", test_port))
    s.listen(1)
    try:
        with pytest.raises(SystemExit):
            singleton.acquire_or_exit()
    finally:
        s.close()
