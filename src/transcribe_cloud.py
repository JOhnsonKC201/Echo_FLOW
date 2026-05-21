"""Groq Whisper cloud transcription — 10x faster than local on free tier."""
from __future__ import annotations

import io
import os
import wave
from dataclasses import dataclass

import numpy as np
import requests


@dataclass
class GroqWhisperConfig:
    model: str = "whisper-large-v3-turbo"   # or "whisper-large-v3"
    language: str | None = None
    api_key_env: str = "GROQ_API_KEY"


class GroqTranscriber:
    def __init__(self, cfg: GroqWhisperConfig):
        self.cfg = cfg
        self.key = os.environ.get(cfg.api_key_env)
        if not self.key:
            raise RuntimeError(f"{cfg.api_key_env} not set")
        self._session = requests.Session()
        self._session.headers.update({"Connection": "keep-alive"})

    @staticmethod
    def _to_wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
        pcm16 = np.clip(audio, -1.0, 1.0)
        pcm16 = (pcm16 * 32767).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm16.tobytes())
        return buf.getvalue()

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> tuple[str, str, dict]:
        if audio.size == 0:
            return "", "en", {"avg_logprob": None, "no_speech_prob": None, "compression_ratio": None}
        wav_bytes = self._to_wav_bytes(audio, sample_rate)
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        data = {
            "model": self.cfg.model,
            "response_format": "verbose_json",
            "temperature": "0",
        }
        if self.cfg.language:
            data["language"] = self.cfg.language
        r = self._session.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {self.key}"},
            files=files,
            data=data,
            timeout=20,
        )
        r.raise_for_status()
        j = r.json()
        # Groq verbose_json includes segments with avg_logprob, no_speech_prob, compression_ratio.
        segs = j.get("segments") or []
        lp_sum = 0.0; lp_n = 0
        ns_max = 0.0
        cr_sum = 0.0; cr_n = 0
        for s in segs:
            if s.get("avg_logprob") is not None:
                lp_sum += float(s["avg_logprob"]); lp_n += 1
            if s.get("no_speech_prob") is not None:
                ns_max = max(ns_max, float(s["no_speech_prob"]))
            if s.get("compression_ratio") is not None:
                cr_sum += float(s["compression_ratio"]); cr_n += 1
        meta = {
            "avg_logprob": (lp_sum / lp_n) if lp_n else None,
            "no_speech_prob": ns_max if segs else None,
            "compression_ratio": (cr_sum / cr_n) if cr_n else None,
        }
        return (j.get("text") or "").strip(), j.get("language", "en"), meta


class HybridTranscriber:
    """Try cloud first; fall back to local on error (offline, rate-limited, etc.)."""

    def __init__(self, cloud, local):
        self.cloud = cloud
        self.local = local

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> tuple[str, str, dict]:
        try:
            return self.cloud.transcribe(audio, sample_rate)
        except Exception as e:
            print(f"[transcribe] cloud failed ({e}); using local")
            return self.local.transcribe(audio, sample_rate)
