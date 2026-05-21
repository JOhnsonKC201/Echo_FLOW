"""Tests for the mobile HTTP bridge.

No real network, no real Whisper, no real Ollama. Uses Flask's test client
and stub Transcriber/Cleaner objects on a synthetic `App` namespace.
"""
from __future__ import annotations

import io
import json
import threading
import time
import types
import wave
from pathlib import Path

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _wav_bytes(sr: int = 16000, duration_s: float = 1.0, freq: float = 440.0,
               amp: float = 0.3, sampwidth: int = 2) -> bytes:
    """Synthesize a sine-wave WAV in memory."""
    n = int(sr * duration_s)
    t = np.arange(n, dtype=np.float32) / sr
    samples = (np.sin(2 * np.pi * freq * t) * amp)
    if sampwidth == 2:
        pcm = np.clip(samples, -1.0, 1.0)
        pcm = (pcm * 32767).astype(np.int16)
        frames = pcm.tobytes()
    elif sampwidth == 4:
        # 32-bit PCM — the bridge should reject this.
        pcm = (np.clip(samples, -1.0, 1.0) * 2147483647).astype(np.int32)
        frames = pcm.tobytes()
    else:
        raise ValueError("unsupported sampwidth in test helper")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(sampwidth)
        w.setframerate(sr)
        w.writeframes(frames)
    return buf.getvalue()


class _StubTranscriber:
    def __init__(self, delay: float = 0.0, return_text: str = "hello world"):
        self.delay = delay
        self.return_text = return_text
        self.calls: list[tuple[int, int]] = []

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000):
        self.calls.append((int(audio.size), int(sample_rate)))
        if self.delay:
            time.sleep(self.delay)
        return self.return_text, "en", {"avg_logprob": -0.1}


class _StubCleaner:
    def __init__(self, delay: float = 0.0, suffix: str = " [cleaned]"):
        self.delay = delay
        self.suffix = suffix
        self.calls: list[tuple[str, str]] = []
        self.augmentations: list[str] = []
        self.provider = "stub"

    def clean(self, text: str, style: str = "default", augmentation: str = "") -> str:
        self.calls.append((text, style))
        self.augmentations.append(augmentation)
        if self.delay:
            time.sleep(self.delay)
        return (text + self.suffix).strip()


class _StubLearner:
    def __init__(self, augmentation: str = ""):
        self.invalidations = 0
        self.augmentation = augmentation
        self.augmentation_calls: list[tuple[str, str]] = []

    def invalidate_cache(self):
        self.invalidations += 1

    def build_prompt_augmentation(self, style: str, query_text: str = "") -> str:
        self.augmentation_calls.append((style, query_text))
        return self.augmentation


class _StubPatternMiner:
    def __init__(self):
        self.records: list[tuple[str, str]] = []

    def record(self, raw: str, cleaned: str):
        self.records.append((raw, cleaned))


class _StubRetriever:
    def __init__(self, vec_value: float = 0.5):
        self.vec_value = vec_value

    def embed_text(self, text: str):
        import numpy as _np
        return _np.full(384, self.vec_value, dtype=_np.float32)

    @staticmethod
    def model_name():
        return "stub-embed-v1"


def _make_app_ref(history=None, *, cleaner_delay=0.0, transcriber_delay=0.0):
    """Build a synthetic App namespace with the singletons the bridge needs."""
    return types.SimpleNamespace(
        cfg={"mobile": {
            "shared_key": "test-key",
            "default_style": "casual",
            "allow_history_write": True,
        }},
        transcriber=_StubTranscriber(delay=transcriber_delay),
        cleaner=_StubCleaner(delay=cleaner_delay),
        history=history,
        learner=_StubLearner(),
        pattern_miner=_StubPatternMiner(),
        phase=types.SimpleNamespace(name="manual"),
        _pipeline_lock=threading.RLock(),
    )


@pytest.fixture
def client_and_app(temp_db):
    """Flask test client wired to a stub App + a real History (temp_db)."""
    from src import bridge
    h, _ = temp_db
    app_ref = _make_app_ref(history=h)
    flask_app = bridge._make_app(
        app_ref,
        shared_key="test-key",
        default_style="casual",
        allow_history_write=True,
    )
    flask_app.config["TESTING"] = True
    return flask_app.test_client(), app_ref


# ---------------------------------------------------------------------------
# Health + auth
# ---------------------------------------------------------------------------

def test_health_no_auth_required(client_and_app):
    client, _ = client_and_app
    r = client.get("/v1/health")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert "providers" in data


def test_auth_missing_key_401(client_and_app):
    client, _ = client_and_app
    r = client.post("/v1/cleanup", json={"text": "hi"})
    assert r.status_code == 401


def test_auth_wrong_key_401(client_and_app):
    client, _ = client_and_app
    r = client.post("/v1/cleanup",
                    json={"text": "hi"},
                    headers={"X-Echo-Key": "nope"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /v1/cleanup
# ---------------------------------------------------------------------------

def test_cleanup_returns_cleaned_text(client_and_app):
    client, app_ref = client_and_app
    r = client.post("/v1/cleanup",
                    json={"text": "raw input", "style": "email"},
                    headers={"X-Echo-Key": "test-key"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["text"] == "raw input [cleaned]"
    assert data["style"] == "email"
    # Cleaner was called with the explicit style
    assert app_ref.cleaner.calls[-1] == ("raw input", "email")


def test_cleanup_defaults_to_configured_style(client_and_app):
    client, app_ref = client_and_app
    client.post("/v1/cleanup",
                json={"text": "x"},
                headers={"X-Echo-Key": "test-key"})
    assert app_ref.cleaner.calls[-1][1] == "casual"


# ---------------------------------------------------------------------------
# /v1/transcribe
# ---------------------------------------------------------------------------

def test_transcribe_decodes_pcm16_wav(client_and_app):
    client, app_ref = client_and_app
    wav = _wav_bytes(sr=16000, duration_s=1.0)
    r = client.post("/v1/transcribe",
                    data={"file": (io.BytesIO(wav), "audio.wav")},
                    headers={"X-Echo-Key": "test-key"},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    data = r.get_json()
    assert data["text"] == "hello world"
    assert data["language"] == "en"
    # Stub received numpy at the configured sample rate
    n_samples, sr_seen = app_ref.transcriber.calls[-1]
    assert sr_seen == 16000
    assert n_samples == 16000  # 1 second at 16 kHz


def test_transcribe_rejects_non_pcm16_415(client_and_app):
    client, _ = client_and_app
    wav = _wav_bytes(sr=16000, duration_s=1.0, sampwidth=4)
    r = client.post("/v1/transcribe",
                    data={"file": (io.BytesIO(wav), "audio.wav")},
                    headers={"X-Echo-Key": "test-key"},
                    content_type="multipart/form-data")
    assert r.status_code == 415


def test_transcribe_too_short_returns_empty(client_and_app):
    client, _ = client_and_app
    wav = _wav_bytes(sr=16000, duration_s=0.1)  # 100ms < MIN_DURATION_MS
    r = client.post("/v1/transcribe",
                    data={"file": (io.BytesIO(wav), "audio.wav")},
                    headers={"X-Echo-Key": "test-key"},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    data = r.get_json()
    assert data["text"] == ""
    assert data["reason"] == "too_short"


def test_transcribe_too_quiet_returns_empty(client_and_app):
    client, _ = client_and_app
    wav = _wav_bytes(sr=16000, duration_s=1.0, amp=0.0001)
    r = client.post("/v1/transcribe",
                    data={"file": (io.BytesIO(wav), "audio.wav")},
                    headers={"X-Echo-Key": "test-key"},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    data = r.get_json()
    assert data["text"] == ""
    assert data["reason"] == "too_quiet"


# ---------------------------------------------------------------------------
# /v1/dictate (full pipeline)
# ---------------------------------------------------------------------------

def test_dictate_full_pipeline_writes_history(client_and_app):
    client, app_ref = client_and_app
    wav = _wav_bytes(sr=16000, duration_s=1.0)
    r = client.post("/v1/dictate?source=iOS&style=casual",
                    data={"file": (io.BytesIO(wav), "audio.wav")},
                    headers={"X-Echo-Key": "test-key"},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    data = r.get_json()
    assert data["raw"] == "hello world"
    assert data["cleaned"] == "hello world [cleaned]"
    assert data["source"] == "Mobile:iOS"
    assert data["style"] == "casual"
    # History row written with the right window_title
    h = app_ref.history
    rows = h.conn.execute(
        "SELECT window_title, style, raw_text, cleaned_text FROM dictations"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "Mobile:iOS"
    assert rows[0][1] == "casual"
    assert rows[0][2] == "hello world"
    assert rows[0][3] == "hello world [cleaned]"
    # Learner cache invalidated, pattern miner saw the (raw, cleaned) pair
    assert app_ref.learner.invalidations == 1
    assert app_ref.pattern_miner.records == [("hello world", "hello world [cleaned]")]


def test_dictate_source_defaults_when_omitted(client_and_app):
    client, app_ref = client_and_app
    wav = _wav_bytes(sr=16000, duration_s=1.0)
    r = client.post("/v1/dictate",
                    data={"file": (io.BytesIO(wav), "audio.wav")},
                    headers={"X-Echo-Key": "test-key"},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    assert r.get_json()["source"].startswith("Mobile:")


def test_dictate_filters_whisper_hallucinations(temp_db):
    """Short audio + canonical Whisper-on-silence phrase = filtered, not logged."""
    from src import bridge
    h, _ = temp_db
    app_ref = _make_app_ref(history=h)
    app_ref.transcriber.return_text = "Thank you."
    flask_app = bridge._make_app(app_ref, "test-key", "casual", True)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    wav = _wav_bytes(sr=16000, duration_s=0.5)
    r = client.post("/v1/dictate",
                    data={"file": (io.BytesIO(wav), "audio.wav")},
                    headers={"X-Echo-Key": "test-key"},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    data = r.get_json()
    assert data["cleaned"] == ""
    assert data["reason"] == "hallucination_filtered"
    # Nothing written to history
    assert h.conn.execute("SELECT COUNT(*) FROM dictations").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Concurrency: the shared pipeline lock serializes overlapping calls
# ---------------------------------------------------------------------------

def test_pipeline_lock_serializes_concurrent_dictate(temp_db):
    """The shared lock must guarantee at most one transcribe runs at a time.

    Deterministic check: the transcriber stub increments a counter on entry,
    sleeps, decrements on exit. With a working lock, the counter never
    exceeds 1. Timing-based assertions on this were flaky under GC pressure.
    """
    from src import bridge
    h, _ = temp_db
    app_ref = _make_app_ref(history=h)

    in_flight = {"now": 0, "max": 0}
    in_flight_lock = threading.Lock()

    def slow_transcribe(audio, sample_rate=16000):
        with in_flight_lock:
            in_flight["now"] += 1
            if in_flight["now"] > in_flight["max"]:
                in_flight["max"] = in_flight["now"]
        time.sleep(0.05)
        with in_flight_lock:
            in_flight["now"] -= 1
        return "hello world", "en", {}

    app_ref.transcriber.transcribe = slow_transcribe

    flask_app = bridge._make_app(app_ref, "test-key", "casual", True)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    wav = _wav_bytes(sr=16000, duration_s=1.0)

    results: list[int] = []

    def go():
        r = client.post("/v1/dictate",
                        data={"file": (io.BytesIO(wav), "audio.wav")},
                        headers={"X-Echo-Key": "test-key"},
                        content_type="multipart/form-data")
        results.append(r.status_code)

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert results == [200, 200, 200, 200]
    assert in_flight["max"] == 1, f"lock violated: {in_flight['max']} concurrent transcribes"


# ---------------------------------------------------------------------------
# /v1/history
# ---------------------------------------------------------------------------

def test_history_endpoint_returns_recent_rows(client_and_app):
    client, app_ref = client_and_app
    app_ref.history.log(
        window_title="Mobile:iOS", style="casual", language="en",
        duration_ms=1000, raw_text="hi", cleaned_text="Hi.",
    )
    r = client.get("/v1/history?limit=5",
                   headers={"X-Echo-Key": "test-key"})
    assert r.status_code == 200
    items = r.get_json()["items"]
    assert len(items) == 1
    assert items[0]["window_title"] == "Mobile:iOS"
    assert items[0]["cleaned"] == "Hi."


# ---------------------------------------------------------------------------
# ensure_shared_key: autogeneration + write-back
# ---------------------------------------------------------------------------

def test_shared_key_autogeneration_writes_config_back(tmp_path):
    import yaml as _yaml
    from src import bridge as _bridge
    cfg_path = tmp_path / "config.yaml"
    initial = {"mobile": {"enabled": True, "shared_key": ""}}
    cfg_path.write_text(_yaml.safe_dump(initial), encoding="utf-8")
    cfg_in_memory = _yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    key = _bridge.ensure_shared_key(cfg_in_memory, cfg_path)

    assert key and len(key) >= 20
    assert cfg_in_memory["mobile"]["shared_key"] == key
    # Persisted back to disk
    on_disk = _yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert on_disk["mobile"]["shared_key"] == key


def test_shared_key_existing_key_is_preserved(tmp_path):
    import yaml as _yaml
    from src import bridge as _bridge
    cfg_path = tmp_path / "config.yaml"
    initial = {"mobile": {"enabled": True, "shared_key": "preset-key-1234567890"}}
    cfg_path.write_text(_yaml.safe_dump(initial), encoding="utf-8")
    cfg_in_memory = _yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    key = _bridge.ensure_shared_key(cfg_in_memory, cfg_path)
    assert key == "preset-key-1234567890"


# ---------------------------------------------------------------------------
# Audit-pass fixes — additional regressions to guard against
# ---------------------------------------------------------------------------

def test_ensure_shared_key_preserves_yaml_comments(tmp_path):
    """Finding 1: yaml.safe_dump nukes comments. Targeted text edit must not."""
    from src import bridge as _bridge
    cfg_path = tmp_path / "config.yaml"
    original = (
        "# Top-level comment that must survive\n"
        "history:\n"
        "  enabled: true   # inline comment\n"
        "\n"
        "# Mobile bridge: see MOBILE_BRIDGE.md\n"
        "mobile:\n"
        '  enabled: true\n'
        '  shared_key: ""        # auto-generated on first run\n'
        "  port: 8765\n"
    )
    cfg_path.write_text(original, encoding="utf-8")
    cfg = {"mobile": {"enabled": True, "shared_key": ""}}

    key = _bridge.ensure_shared_key(cfg, cfg_path)

    new_text = cfg_path.read_text(encoding="utf-8")
    assert "# Top-level comment that must survive" in new_text
    assert "# inline comment" in new_text
    assert "# Mobile bridge: see MOBILE_BRIDGE.md" in new_text
    assert "# auto-generated on first run" in new_text
    assert f'shared_key: "{key}"' in new_text
    assert 'shared_key: ""' not in new_text


def test_ensure_shared_key_falls_back_when_placeholder_missing(tmp_path):
    """If the user's config has no `shared_key: ""` line, fall back to yaml dump."""
    import yaml as _yaml
    from src import bridge as _bridge
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("history:\n  enabled: true\n", encoding="utf-8")
    cfg = {"history": {"enabled": True}}

    key = _bridge.ensure_shared_key(cfg, cfg_path)

    on_disk = _yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert on_disk["mobile"]["shared_key"] == key


def test_dictate_passes_augmentation_when_learner_present(temp_db):
    """Finding 2: bridge must thread augmentation into cleaner.clean (RAG)."""
    from src import bridge
    h, _ = temp_db
    app_ref = _make_app_ref(history=h)
    app_ref.learner = _StubLearner(augmentation=" [RAG-context]")
    flask_app = bridge._make_app(app_ref, "test-key", "casual", True)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    # 25+ chars so it passes the skip_aug short-circuit
    long_text = "this is a longer dictation that should trigger augmentation"
    app_ref.transcriber.return_text = long_text
    wav = _wav_bytes(sr=16000, duration_s=1.0)

    client.post("/v1/dictate",
                data={"file": (io.BytesIO(wav), "audio.wav")},
                headers={"X-Echo-Key": "test-key"},
                content_type="multipart/form-data")
    assert app_ref.learner.augmentation_calls == [("casual", long_text)]
    assert app_ref.cleaner.augmentations[-1] == " [RAG-context]"


def test_dictate_skips_augmentation_for_short_text(temp_db):
    """Mirror desktop's skip_aug = len(raw) < 25 short-circuit."""
    from src import bridge
    h, _ = temp_db
    app_ref = _make_app_ref(history=h)
    app_ref.learner = _StubLearner(augmentation=" [RAG]")
    flask_app = bridge._make_app(app_ref, "test-key", "casual", True)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    app_ref.transcriber.return_text = "short"   # 5 chars
    wav = _wav_bytes(sr=16000, duration_s=1.0)

    client.post("/v1/dictate",
                data={"file": (io.BytesIO(wav), "audio.wav")},
                headers={"X-Echo-Key": "test-key"},
                content_type="multipart/form-data")
    # Learner never called for short inputs; cleaner gets empty augmentation
    assert app_ref.learner.augmentation_calls == []
    assert app_ref.cleaner.augmentations[-1] == ""


def test_cleanup_endpoint_passes_augmentation(temp_db):
    """/v1/cleanup must also use the learner — phones using text-only path."""
    from src import bridge
    h, _ = temp_db
    app_ref = _make_app_ref(history=h)
    app_ref.learner = _StubLearner(augmentation=" [RAG]")
    flask_app = bridge._make_app(app_ref, "test-key", "casual", True)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()

    client.post("/v1/cleanup",
                json={"text": "this is a longer text body to clean up properly"},
                headers={"X-Echo-Key": "test-key"})
    assert app_ref.cleaner.augmentations[-1] == " [RAG]"


def test_dictate_writes_embedding_when_retriever_present(temp_db):
    """Finding 3: mobile rows must carry an embedding so the retriever sees them."""
    from src import bridge
    h, _ = temp_db
    app_ref = _make_app_ref(history=h)
    app_ref.retriever = _StubRetriever(vec_value=0.25)
    flask_app = bridge._make_app(app_ref, "test-key", "casual", True)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    wav = _wav_bytes(sr=16000, duration_s=1.0)

    r = client.post("/v1/dictate",
                    data={"file": (io.BytesIO(wav), "audio.wav")},
                    headers={"X-Echo-Key": "test-key"},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    row = h.conn.execute(
        "SELECT embedding, embedding_model FROM dictations"
    ).fetchone()
    assert row[0] is not None and len(row[0]) > 0
    assert row[1] == "stub-embed-v1"


def test_dictate_handles_missing_retriever_gracefully(client_and_app):
    """No retriever attribute → null embedding, no crash."""
    client, app_ref = client_and_app
    # Default _make_app_ref has no retriever attr
    assert not hasattr(app_ref, "retriever")
    wav = _wav_bytes(sr=16000, duration_s=1.0)
    r = client.post("/v1/dictate",
                    data={"file": (io.BytesIO(wav), "audio.wav")},
                    headers={"X-Echo-Key": "test-key"},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    row = app_ref.history.conn.execute(
        "SELECT embedding FROM dictations"
    ).fetchone()
    assert row[0] is None


def test_make_app_raises_without_pipeline_lock():
    """Finding 5: bridge must refuse to build without the shared lock."""
    import types as _types
    from src import bridge
    app_ref = _types.SimpleNamespace(cfg={})   # no _pipeline_lock
    with pytest.raises(RuntimeError, match="_pipeline_lock"):
        bridge._make_app(app_ref, "test-key", "casual", True)


def test_serve_refuses_empty_key(temp_db, monkeypatch):
    """Finding 6: serve() must refuse to bind if shared_key is empty."""
    from src import bridge
    h, _ = temp_db
    app_ref = _make_app_ref(history=h)
    app_ref.cfg["mobile"]["shared_key"] = ""

    # Trip if it tries to actually bind a port
    def boom(*a, **kw):
        raise AssertionError("serve() must not call make_server when key is empty")
    monkeypatch.setattr("werkzeug.serving.make_server", boom)

    logged: list[str] = []
    bridge.serve(app_ref, "127.0.0.1", 18765, log_fn=logged.append)
    assert any("not started" in m.lower() for m in logged)


def test_max_content_length_is_8mb():
    """Finding 7: cap should be 8 MB, not 25 MB."""
    from src import bridge
    import types as _types, threading as _thr
    app_ref = _types.SimpleNamespace(cfg={}, _pipeline_lock=_thr.RLock())
    flask_app = bridge._make_app(app_ref, "test-key", "casual", True)
    assert flask_app.config["MAX_CONTENT_LENGTH"] == 8 * 1024 * 1024
