"""Microphone capture with optional Silero VAD-based auto-stop."""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass

import numpy as np

from . import log as wlog

# sounddevice (PortAudio) is imported lazily inside start() so this module and
# its importers (src.main, the dashboard, the test suite) load on machines
# without PortAudio / an audio backend installed.

_log = wlog.get("audio")


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
        self._vad_warned = False
        if cfg.vad_enabled:
            try:
                from silero_vad import load_silero_vad
                self._vad = load_silero_vad()
            except Exception as e:
                _log.exception(f"Suppressed in Recorder.__init__ (silero_vad load): {e}")
                self._vad = None

    def _callback(self, indata, frames, time_info, status):
        if status:
            pass
        self._q.put(indata.copy())

    def start(self):
        import sounddevice as sd
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
        # try/except: InputStream(...) already opened the device, so if start()
        # raises (exclusive-mode conflict, sample-rate mismatch) we must close
        # the handle ourselves, sounddevice's stream defines no __del__, so a
        # dropped reference orphans the device and its callback thread for the
        # life of the process.
        try:
            self._stream.start()
        except Exception:
            try:
                self._stream.close()
            except Exception as e:
                _log.warning("audio stream close after failed start: %s", e)
            self._stream = None
            raise
        self._recording = True

    def stop(self) -> np.ndarray:
        if not self._recording:
            return np.zeros(0, dtype=np.float32)
        # try/finally: if _stream.stop() raises (e.g. PortAudio error on device
        # removal), we must still close the stream and clear _recording —
        # otherwise the handle leaks AND start() early-returns forever, wedging
        # the recorder until restart.
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception as e:
            _log.warning("audio stream stop/close failed: %s", e)
        finally:
            self._stream = None
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
        except Exception as e:
            _log.exception(f"Suppressed in record_until_silence (torch import): {e}")
            vad = None

        # Drain the queue each tick into `collected` (thread-safe via
        # get_nowait — never reach into self._q.queue while the audio callback
        # thread is appending to it, which can raise "deque mutated during
        # iteration"). Keep a rolling window of the last 10 chunks for VAD.
        collected: list[np.ndarray] = []
        while self._recording and (time.time() - start_t) < max_seconds:
            time.sleep(0.05)
            drained = False
            while True:
                try:
                    collected.append(self._q.get_nowait())
                    drained = True
                except queue.Empty:
                    break
            if not drained:
                continue
            recent = collected[-10:]
            sample = np.concatenate(recent, axis=0).flatten().astype(np.float32)
            voiced = self._is_voiced(sample, vad)
            if voiced:
                last_voice_t = time.time()
            elif (time.time() - last_voice_t) * 1000 > self.cfg.silence_timeout_ms:
                break
        # Stop the stream and append any chunks that arrived after the last
        # drain, then return the full recording.
        tail = self.stop()
        if collected:
            head = np.concatenate(collected, axis=0).flatten().astype(np.float32)
            if tail.size:
                return np.concatenate([head, tail], axis=0).astype(np.float32)
            return head
        return tail

    def _rms_voiced(self, sample: np.ndarray) -> bool:
        return float(np.sqrt(np.mean(sample**2))) > 0.01

    def _is_voiced(self, sample: np.ndarray, vad) -> bool:
        if vad is None:
            return self._rms_voiced(sample)
        try:
            import torch
            # Silero v5+ accepts EXACTLY one window per call: 512 samples at
            # 16 kHz, 256 at 8 kHz. Anything else raises. Our capture blocks are
            # 30 ms (480 frames at 16 kHz) and the rolling window is 1-10 of
            # them, so the old code's flat `sample[-2048:]` was never a legal
            # size, every call raised and fell through to the RMS branch, which
            # meant Silero never actually ran despite being loaded at startup.
            win = 256 if self.cfg.sample_rate < 16000 else 512
            tail = sample[-win * 4:]
            if tail.size < win:
                return True
            n_win = tail.size // win
            frames = tail[tail.size - n_win * win:].reshape(n_win, win)
            # Silero is stateful across calls; reset so a rolling window that
            # re-feeds overlapping audio each tick can't poison the LSTM state.
            try:
                vad.reset_states()
            except Exception:
                pass
            best = 0.0
            for f in frames:
                t = torch.from_numpy(np.ascontiguousarray(f))
                best = max(best, float(vad(t.unsqueeze(0), self.cfg.sample_rate).item()))
            return best > 0.5
        except Exception as e:
            # Warn once, this runs every 50 ms, so logging each failure would
            # flood the log, which is how the size bug above stayed invisible.
            if not self._vad_warned:
                self._vad_warned = True
                _log.warning("silero VAD failed, falling back to RMS: %s", e)
            return self._rms_voiced(sample)
