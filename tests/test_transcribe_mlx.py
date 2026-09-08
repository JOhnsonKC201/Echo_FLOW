"""The Apple silicon path: mlx-whisper behind the same Transcriber.

Everything here injects a fake ``mlx_whisper`` module, so the file passes on
Windows and on the macOS CI runner alike; nothing downloads or runs a model.
"""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from src import transcribe as T


def _result(text="hello there", language="en", words=None, **extra):
    seg = {
        "text": f" {text} ", "avg_logprob": -0.25, "no_speech_prob": 0.05,
        "compression_ratio": 1.3,
    }
    if words is not None:
        seg["words"] = words
    seg.update(extra)
    return {"text": text, "segments": [seg], "language": language}


def _install_fake_mlx(monkeypatch, *, result=None, fail=False, calls=None):
    """``import mlx_whisper`` yields a module whose transcribe() we script."""
    def transcribe(audio, **kw):
        if calls is not None:
            calls.append(kw)
        if fail:
            raise RuntimeError("Metal device not found")
        return result if result is not None else _result()
    mod = types.ModuleType("mlx_whisper")
    mod.transcribe = transcribe
    monkeypatch.setitem(sys.modules, "mlx_whisper", mod)
    return mod


def _install_fake_faster_whisper(monkeypatch, built):
    class _Model:
        def __init__(self, name, device=None, compute_type=None):
            self.name, self.device, self.compute_type = name, device, compute_type
            built.append(self)

        def transcribe(self, audio, **kw):
            return iter(()), types.SimpleNamespace(language="en")

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = _Model
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    ct2 = types.ModuleType("ctranslate2")
    ct2.get_cuda_device_count = lambda: 0
    monkeypatch.setitem(sys.modules, "ctranslate2", ct2)


# --- is this machine an MLX machine? ------------------------------------------------

def test_mlx_needs_a_mac(monkeypatch):
    _install_fake_mlx(monkeypatch)
    assert T._mlx_is_usable("win32", "AMD64") is False
    assert T._mlx_is_usable("linux", "aarch64") is False


def test_mlx_needs_apple_silicon_not_rosetta(monkeypatch):
    _install_fake_mlx(monkeypatch)
    assert T._mlx_is_usable("darwin", "x86_64") is False
    assert T._mlx_is_usable("darwin", "arm64") is True


def test_mlx_needs_the_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "mlx_whisper", None)   # import raises
    assert T._mlx_is_usable("darwin", "arm64") is False


# --- model names ------------------------------------------------------------------

@pytest.mark.parametrize("name,repo", [
    ("base", "mlx-community/whisper-base-mlx"),
    ("large-v3-turbo", "mlx-community/whisper-large-v3-turbo"),
    ("large-v3", "mlx-community/whisper-large-v3-mlx"),
    ("distil-large-v3", "mlx-community/whisper-distil-large-v3-mlx"),   # unknown: the convention
    ("someone/whisper-turbo-q4", "someone/whisper-turbo-q4"),           # a repo passes through
])
def test_mlx_repo_maps_config_names(name, repo):
    assert T.mlx_repo(name) == repo


def test_auto_model_is_turbo_on_mlx_like_cuda():
    cfg = T.WhisperConfig(model="auto", compute_type="auto")
    assert T._resolve(cfg, "mlx") == ("large-v3-turbo", "float16")
    assert T._resolve(cfg, "cuda") == ("large-v3-turbo", "float16")
    assert T._resolve(cfg, "cpu") == ("base", "int8")


# --- the adapter --------------------------------------------------------------------

def test_adapter_calls_mlx_with_the_repo_and_language():
    calls = []

    def fake(audio, **kw):
        calls.append(kw)
        return _result()

    m = T.MlxWhisperModel("large-v3-turbo", transcribe_fn=fake)
    segments, info = m.transcribe(
        np.zeros(16000, dtype=np.float32), language="en", beam_size=5,
        vad_filter=True, condition_on_previous_text=False,
        initial_prompt="FastAPI, Supabase", word_timestamps=True,
    )
    assert calls[0]["path_or_hf_repo"] == "mlx-community/whisper-large-v3-turbo"
    assert calls[0]["language"] == "en"
    assert calls[0]["initial_prompt"] == "FastAPI, Supabase"
    assert calls[0]["word_timestamps"] is True
    assert calls[0]["condition_on_previous_text"] is False
    # mlx-whisper raises NotImplementedError on beam_size and has no VAD.
    assert "beam_size" not in calls[0] and "vad_filter" not in calls[0]
    assert info.language == "en"
    seg = list(segments)[0]
    assert seg.text.strip() == "hello there"
    assert seg.avg_logprob == -0.25 and seg.no_speech_prob == 0.05 and seg.compression_ratio == 1.3


def test_adapter_leaves_language_out_when_auto_detecting():
    calls = []
    m = T.MlxWhisperModel("base", transcribe_fn=lambda a, **kw: calls.append(kw) or _result(language="fr"))
    _, info = m.transcribe(np.zeros(8), language=None)
    assert "language" not in calls[0]
    assert info.language == "fr"


def test_adapter_maps_words_and_tolerates_missing_fields():
    words = [{"word": " FastAPI", "probability": 0.41}, {"word": " is", "probability": 0.99}]
    m = T.MlxWhisperModel("base", transcribe_fn=lambda a, **kw: _result(words=words))
    seg = list(m.transcribe(np.zeros(8))[0])[0]
    assert [(w.word, w.probability) for w in seg.words] == [(" FastAPI", 0.41), (" is", 0.99)]
    bare = T.MlxWhisperModel("base", transcribe_fn=lambda a, **kw: {"segments": [{"text": "x"}]})
    seg = list(bare.transcribe(np.zeros(8))[0])[0]
    assert seg.words == [] and seg.avg_logprob is None
    empty = T.MlxWhisperModel("base", transcribe_fn=lambda a, **kw: None)
    segs, info = empty.transcribe(np.zeros(8), language="en")
    assert list(segs) == [] and info.language == "en"


# --- through the Transcriber ---------------------------------------------------------

def test_auto_picks_mlx_on_apple_silicon_and_transcribes(monkeypatch):
    calls = []
    _install_fake_mlx(monkeypatch, calls=calls)
    monkeypatch.setattr(T, "_mlx_is_usable", lambda platform=None, machine=None: True)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)   # must not be needed

    t = T.Transcriber(T.WhisperConfig(model="auto", device="auto", compute_type="auto"))

    assert t.resolved_device == "mlx"
    assert t.resolved_model == "large-v3-turbo"
    assert len(calls) == 1                     # the startup probe
    text, lang, meta = t.transcribe(np.zeros(16000 * 4, dtype=np.float32))
    assert text == "hello there" and lang == "en"
    assert meta["avg_logprob"] == -0.25 and meta["no_speech_prob"] == 0.05
    assert calls[-1]["path_or_hf_repo"] == "mlx-community/whisper-large-v3-turbo"


def test_low_confidence_words_flow_through_from_mlx(monkeypatch):
    words = [{"word": " Supabase", "probability": 0.3}, {"word": " works", "probability": 0.97}]
    _install_fake_mlx(monkeypatch, result=_result(words=words))
    monkeypatch.setattr(T, "_mlx_is_usable", lambda platform=None, machine=None: True)
    t = T.Transcriber(T.WhisperConfig(model="base", device="mlx", word_confidence=True))
    _, _, meta = t.transcribe(np.zeros(16000, dtype=np.float32))
    assert meta["low_conf_words"] == [("Supabase", 0.3)]


def test_failed_mlx_probe_falls_back_to_cpu_faster_whisper(monkeypatch):
    _install_fake_mlx(monkeypatch, fail=True)
    monkeypatch.setattr(T, "_mlx_is_usable", lambda platform=None, machine=None: True)
    built = []
    _install_fake_faster_whisper(monkeypatch, built)

    t = T.Transcriber(T.WhisperConfig(model="auto", device="auto", compute_type="auto"))

    assert t.resolved_device == "cpu"
    assert t.resolved_model == "base"
    assert built[0].compute_type == "int8"


def test_explicit_mlx_without_the_package_falls_back_to_cpu(monkeypatch):
    monkeypatch.setitem(sys.modules, "mlx_whisper", None)
    built = []
    _install_fake_faster_whisper(monkeypatch, built)

    t = T.Transcriber(T.WhisperConfig(model="auto", device="mlx"))

    assert t.resolved_device == "cpu" and t.resolved_model == "base"


def test_non_apple_machines_never_touch_mlx(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("mlx must not be built here")
    monkeypatch.setattr(T, "_build_mlx", boom)
    monkeypatch.setattr(T, "_mlx_is_usable", lambda platform=None, machine=None: False)
    built = []
    _install_fake_faster_whisper(monkeypatch, built)
    t = T.Transcriber(T.WhisperConfig(model="auto", device="auto"))
    assert t.resolved_device == "cpu"
