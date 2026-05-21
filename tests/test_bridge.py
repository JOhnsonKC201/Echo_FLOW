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
        self.provider = "stub"

    def clean(self, text: str, style: str = "default", augmentation: str = "") -> str:
        self.calls.append((text, style))
        if self.delay:
            time.sleep(self.delay)
        return (text + self.suffix).strip()


class _StubLearner:
    def __init__(self):
        self.invalidations = 0

    def invalidate_cache(self):
        self.invalidations += 1


class _StubPatternMiner:
    def __init__(self):
        self.records: list[tuple[str, str]] = []

    def record(self, raw: str, cleaned: str):
        self.records.append((raw, cleaned))


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
    from src import bridge
    h, _ = temp_db
    # Simulate a slow transcriber so two requests would overlap without the lock.
    app_ref = _make_app_ref(history=h, transcriber_delay=0.2)
    flask_app = bridge._make_app(app_ref, "test-key", "casual", True)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    wav = _wav_bytes(sr=16000, duration_s=1.0)

    intervals: list[tuple[float, float]] = []
    lock = threading.Lock()

    def go():
        t0 = time.time()
        r = client.post("/v1/dictate",
                        data={"file": (io.BytesIO(wav), "audio.wav")},
                        headers={"X-Echo-Key": "test-key"},
                        content_type="multipart/form-data")
        t1 = time.time()
        with lock:
            intervals.append((t0, t1))
        assert r.status_code == 200

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join()
    intervals.sort()
    # If the lock serializes, the second request finishes after the first
    # finishes, not 200ms after both started. Total wall time ≥ 2 * 0.2s.
    assert (intervals[1][1] - intervals[0][0]) >= 0.35


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
