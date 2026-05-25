"""Smoke tests — lock down current correct behavior so refactors are safe."""
import sqlite3
from pathlib import Path

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

    out, skipped = cleaner.clean("Let's ship the migration tonight.")
    assert out == "Let's ship the migration tonight."
    assert skipped is True


def test_clean_calls_llm_on_messy_input(monkeypatch):
    from src.cleanup import Cleaner
    cleaner = Cleaner({"enabled": True, "provider": "ollama"})
    called = []

    def _fake(prompt, text, **kwargs):
        called.append(text)
        return "Cleaned output."
    monkeypatch.setattr(cleaner, "_via_ollama", _fake)

    out, skipped = cleaner.clean("um yeah like ship the thing")
    assert called, "LLM should have been called on filler-heavy input"
    assert out == "Cleaned output."
    assert skipped is False


def test_skip_path_runs_even_with_augmentation(monkeypatch):
    """Clean input with augmentation should STILL skip the LLM."""
    from src.cleanup import Cleaner
    cleaner = Cleaner({"enabled": True, "provider": "ollama"})

    def _boom(*a, **k):
        raise AssertionError("LLM was called despite clean input")
    monkeypatch.setattr(cleaner, "_via_ollama", _boom)

    out, skipped = cleaner.clean("Let's ship the migration tonight.",
                                  augmentation="\nFew-shot examples:\n...")
    assert out == "Let's ship the migration tonight."
    assert skipped is True


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
    # H4: max_tokens now passed as an explicit kwarg, not an instance field.
    out = cleaner._via_ollama("sys", "user", max_tokens=700)
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


# --- H2: warmup propagates HTTP errors ---------------------------------------

def test_warmup_raises_on_http_error(monkeypatch):
    """warmup() must call raise_for_status() and not log 'model loaded' on a 4xx."""
    from src import cleanup as cleanup_mod
    from src.cleanup import Cleaner
    cleaner = Cleaner({"enabled": True, "provider": "ollama",
                       "ollama": {"model": "missing-model"}})

    raised = []

    class _BadResp:
        status_code = 404
        def raise_for_status(self):
            raised.append(True)
            import requests
            raise requests.HTTPError("404 model not found")
        def json(self): return {}

    def _fake_post(*a, **k):
        return _BadResp()
    monkeypatch.setattr(cleaner._session, "post", _fake_post)

    # Spy on the module logger so we can confirm "loaded" is NOT logged.
    info_calls = []
    monkeypatch.setattr(cleanup_mod._log, "info",
                        lambda msg, *a, **k: info_calls.append(msg % a if a else msg))
    cleaner.warmup()
    assert raised, "warmup() must call raise_for_status() on the response"
    assert not any("loaded" in m.lower() for m in info_calls), \
        f"warmup() must not log 'model loaded' on 4xx; got: {info_calls}"


# --- H3: clean() returns (text, skipped) tuple --------------------------------

def test_clean_returns_tuple_and_skipped_flag(monkeypatch):
    from src.cleanup import Cleaner
    cleaner = Cleaner({"enabled": True, "provider": "ollama"})

    # Already-clean: skip path → skipped=True, LLM never called.
    monkeypatch.setattr(cleaner, "_via_ollama",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    text, skipped = cleaner.clean("Ship the migration tonight.")
    assert isinstance(text, str)
    assert skipped is True


def test_clean_returns_tuple_when_llm_runs(monkeypatch):
    from src.cleanup import Cleaner
    cleaner = Cleaner({"enabled": True, "provider": "ollama"})
    monkeypatch.setattr(cleaner, "_via_ollama", lambda *a, **k: "Cleaned.")
    text, skipped = cleaner.clean("um yeah like ship the thing")
    assert text == "Cleaned."
    assert skipped is False


def test_clean_with_returns_tuple(monkeypatch):
    from src.cleanup import Cleaner
    cleaner = Cleaner({"enabled": True, "provider": "ollama"})
    monkeypatch.setattr(cleaner, "_via_ollama", lambda *a, **k: "Polished.")
    text, skipped = cleaner.clean_with("ollama", "um yeah ship it")
    assert text == "Polished."
    assert skipped is False


def test_clean_empty_input_returns_tuple():
    from src.cleanup import Cleaner
    cleaner = Cleaner({"enabled": True, "provider": "ollama"})
    out, skipped = cleaner.clean("")
    assert out == ""
    assert skipped is False


# --- H4: no leaked instance state between clean() calls ----------------------

def test_clean_does_not_leak_instance_state(monkeypatch):
    """Cleaner must not stash per-call state (style/max_tokens) on self."""
    from src.cleanup import Cleaner
    cleaner = Cleaner({"enabled": True, "provider": "ollama"})
    monkeypatch.setattr(cleaner, "_via_ollama", lambda *a, **k: "x.")
    cleaner.clean("um whatever", style="email", max_tokens_override=500)
    assert not hasattr(cleaner, "_current_style"), \
        "Cleaner leaked _current_style onto self — race waiting to happen"
    assert not hasattr(cleaner, "_max_tokens_override"), \
        "Cleaner leaked _max_tokens_override onto self — race waiting to happen"


def test_via_ollama_uses_kwarg_not_instance_field(monkeypatch):
    """_via_ollama must read max_tokens from its kwarg, not self.*."""
    from src.cleanup import Cleaner
    cleaner = Cleaner({"enabled": True, "provider": "ollama"})
    captured = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": "ok"}}

    def _fake_post(url, json=None, timeout=None, **k):
        captured["json"] = json
        return _Resp()
    monkeypatch.setattr(cleaner._session, "post", _fake_post)
    cleaner._via_ollama("sys", "user", max_tokens=123)
    assert captured["json"]["options"].get("num_predict") == 123
    # And: without the kwarg, num_predict is absent (no leaked state).
    captured.clear()
    cleaner._via_ollama("sys", "user")
    assert "num_predict" not in captured["json"]["options"]


# --- M7: adaptive beam_size for short clips ----------------------------------

def test_short_audio_uses_greedy_beam(monkeypatch):
    """Audio < 3s → beam_size=1; >= 3s → cfg.beam_size."""
    from src.transcribe import Transcriber, WhisperConfig

    cfg = WhisperConfig(model="tiny", beam_size=5)
    # Avoid actually loading a model.
    t = Transcriber.__new__(Transcriber)
    t.cfg = cfg
    captured = {}

    class _Info: language = "en"

    def _fake_transcribe(audio, **kwargs):
        captured["beam_size"] = kwargs.get("beam_size")
        return iter([]), _Info()

    t.model = type("M", (), {"transcribe": staticmethod(_fake_transcribe)})()

    # 2 seconds at 16kHz → greedy.
    audio_short = np.zeros(16000 * 2, dtype=np.float32)
    t.transcribe(audio_short, 16000)
    assert captured["beam_size"] == 1, "<3s clip should use beam_size=1"

    # 5 seconds → configured beam.
    audio_long = np.zeros(16000 * 5, dtype=np.float32)
    t.transcribe(audio_long, 16000)
    assert captured["beam_size"] == 5, ">=3s clip should use cfg.beam_size"


# --- M8: focused title cached at press time ----------------------------------

def test_press_title_field_exists_and_default_none():
    """App must have a _press_title cache that defaults to None."""
    # Avoid full App() init (heavy deps). Just confirm the attribute is
    # initialized via the field default by reading the source.
    import src.main as m
    src_text = (Path(m.__file__)).read_text(encoding="utf-8")
    assert "self._press_title" in src_text, \
        "App._press_title field missing — M8 not wired"
    assert "self._press_title = None" in src_text, \
        "App._press_title must default to None"
    # And: on_press_hold / on_toggle must populate it.
    assert "self._press_title = self.injector.focused_title()" in src_text, \
        "hotkey handlers must cache focused_title() at press time"


def test_press_title_consumed_in_do_dictation():
    """_do_dictation should prefer cached title and clear it after use."""
    import src.main as m
    src_text = (Path(m.__file__)).read_text(encoding="utf-8")
    # Must read from cache and clear it.
    assert "title = self._press_title" in src_text
    assert "self._press_title = None" in src_text


# --- M9: learned + skip-clean still applies pattern substitution -------------

def test_skip_path_applies_learned_patterns_when_provider_learned(monkeypatch):
    """If provider='learned' and input is 'clean enough' to skip the LLM,
    high-confidence learned patterns must still be applied."""
    from src.cleanup import Cleaner

    class _Miner:
        def confident_patterns(self, min_confidence):
            return {"migration": "Migration2024"}

    cleaner = Cleaner({
        "enabled": True, "provider": "learned",
        "learned": {"min_pattern_confidence": 0.5, "fallback_to_ollama": False},
    })
    cleaner.attach_learning(_Miner(), None)
    # Sentence is already-clean per heuristic.
    out, skipped = cleaner.clean("Ship the migration tonight.")
    assert skipped is True
    assert "Migration2024" in out, \
        f"skip path with learned provider must apply patterns; got: {out!r}"


def test_skip_path_no_patterns_when_provider_ollama(monkeypatch):
    """Sanity check: provider='ollama' skip path doesn't run pattern subst."""
    from src.cleanup import Cleaner

    class _Miner:
        def confident_patterns(self, min_confidence):
            return {"migration": "Migration2024"}

    cleaner = Cleaner({"enabled": True, "provider": "ollama"})
    cleaner.attach_learning(_Miner(), None)
    out, skipped = cleaner.clean("Ship the migration tonight.")
    assert skipped is True
    assert "Migration2024" not in out


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
