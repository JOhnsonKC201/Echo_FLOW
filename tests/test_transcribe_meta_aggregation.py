"""Transcriber.transcribe() aggregates per-segment grading signals.

avg_logprob = mean, no_speech_prob = MAX (existing code), compression_ratio = mean.
Also asserts cfg.initial_prompt is forwarded to model.transcribe(initial_prompt=).
"""
from __future__ import annotations

import types

import numpy as np
import pytest


def _seg(text, avg_logprob, no_speech_prob, compression_ratio):
    return types.SimpleNamespace(
        text=text,
        avg_logprob=avg_logprob,
        no_speech_prob=no_speech_prob,
        compression_ratio=compression_ratio,
    )


def _make_transcriber_with_segments(segments, *, initial_prompt=None):
    from src.transcribe import Transcriber, WhisperConfig
    cfg = WhisperConfig(model="tiny", beam_size=5, initial_prompt=initial_prompt)
    t = Transcriber.__new__(Transcriber)
    t.cfg = cfg
    captured: dict = {}

    class _Info: language = "en"

    def _fake_transcribe(audio, **kwargs):
        captured.update(kwargs)
        return iter(segments), _Info()

    t.model = type("M", (), {"transcribe": staticmethod(_fake_transcribe)})()
    return t, captured


def test_meta_aggregation_logprob_mean_nospeech_max_cr_mean():
    segs = [
        _seg("hello",   avg_logprob=-0.1, no_speech_prob=0.1, compression_ratio=1.0),
        _seg("world",   avg_logprob=-0.3, no_speech_prob=0.5, compression_ratio=2.0),
        _seg("again",   avg_logprob=-0.5, no_speech_prob=0.2, compression_ratio=3.0),
    ]
    t, _ = _make_transcriber_with_segments(segs)
    # 5 seconds so beam_size stays at cfg.beam_size (not the short-clip greedy path).
    audio = np.zeros(16000 * 5, dtype=np.float32)
    text, lang, meta = t.transcribe(audio, 16000)

    assert text == "hello world again"
    assert lang == "en"
    assert meta["avg_logprob"] == pytest.approx((-0.1 + -0.3 + -0.5) / 3)
    # IMPORTANT: existing code aggregates no_speech_prob as MAX, not mean.
    assert meta["no_speech_prob"] == pytest.approx(0.5)
    assert meta["compression_ratio"] == pytest.approx((1.0 + 2.0 + 3.0) / 3)


def test_initial_prompt_forwarded_to_model():
    segs = [_seg("hi", -0.2, 0.1, 1.5)]
    prompt = "Vocabulary: FastAPI, Supabase, node2vec"
    t, captured = _make_transcriber_with_segments(segs, initial_prompt=prompt)
    audio = np.zeros(16000 * 5, dtype=np.float32)
    t.transcribe(audio, 16000)
    assert captured.get("initial_prompt") == prompt


def test_initial_prompt_none_when_unset():
    segs = [_seg("hi", -0.2, 0.1, 1.5)]
    t, captured = _make_transcriber_with_segments(segs, initial_prompt=None)
    audio = np.zeros(16000 * 5, dtype=np.float32)
    t.transcribe(audio, 16000)
    # Key must be present and explicitly None — not silently dropped.
    assert "initial_prompt" in captured
    assert captured["initial_prompt"] is None
