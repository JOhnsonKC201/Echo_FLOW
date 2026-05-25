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
