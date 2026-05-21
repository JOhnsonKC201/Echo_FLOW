"""Microphone capture with optional Silero VAD-based auto-stop."""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass

import numpy as np
import sounddevice as sd


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    device: int | None = None
    vad_enabled: bool = True
    silence_timeout_ms: int = 1500


class Recorder:
    """Streaming recorder. start() begins capture; stop() returns float32 mono PCM."""

    def __init__(self, cfg: AudioConfig):
        self.cfg = cfg
        self._q: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: sd.InputStream | None = None
        self._recording = False
        self._vad = None
        if cfg.vad_enabled:
            try:
                from silero_vad import load_silero_vad
                self._vad = load_silero_vad()
            except Exception:
                self._vad = None

    def _callback(self, indata, frames, time_info, status):
        if status:
            pass
        self._q.put(indata.copy())

    def start(self):
        if self._recording:
            return
        self._q = queue.Queue()
        self._stream = sd.InputStream(
            samplerate=self.cfg.sample_rate,
            channels=self.cfg.channels,
            device=self.cfg.device,
            dtype="float32",
            callback=self._callback,
            blocksize=int(self.cfg.sample_rate * 0.03),  # 30ms
        )
        self._stream.start()
        self._recording = True

    def stop(self) -> np.ndarray:
        if not self._recording:
            return np.zeros(0, dtype=np.float32)
        self._stream.stop()
        self._stream.close()
        self._recording = False
        chunks = []
        while not self._q.empty():
            chunks.append(self._q.get())
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(chunks, axis=0).flatten().astype(np.float32)
        return audio

    def record_until_silence(self, max_seconds: float = 60.0) -> np.ndarray:
        """Toggle-mode record: keep going until VAD reports silence_timeout_ms of quiet."""
        self.start()
        start_t = time.time()
        last_voice_t = time.time()
        try:
            import torch
            vad = self._vad
        except Exception:
            vad = None

        # Simple energy-based fallback if VAD missing
        while self._recording and (time.time() - start_t) < max_seconds:
            time.sleep(0.05)
            # Peek queue without draining
            if self._q.empty():
                continue
            recent = list(self._q.queue)[-10:]
            if not recent:
                continue
            sample = np.concatenate(recent, axis=0).flatten().astype(np.float32)
            voiced = self._is_voiced(sample, vad)
            if voiced:
                last_voice_t = time.time()
            elif (time.time() - last_voice_t) * 1000 > self.cfg.silence_timeout_ms:
                break
        return self.stop()

    def _is_voiced(self, sample: np.ndarray, vad) -> bool:
        if vad is None:
            return float(np.sqrt(np.mean(sample**2))) > 0.01
        try:
            import torch
            t = torch.from_numpy(sample[-512 * 4:])
            if t.numel() < 512:
                return True
            prob = vad(t.unsqueeze(0) if t.ndim == 1 else t, self.cfg.sample_rate).item()
            return prob > 0.5
        except Exception:
            return float(np.sqrt(np.mean(sample**2))) > 0.01
