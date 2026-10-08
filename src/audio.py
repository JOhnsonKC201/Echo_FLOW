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

# Below this level a clip is silence. Whisper hallucinates "Thank you" on
# silence, so such clips are dropped before they reach it.
SILENCE_RMS = 0.003

# Inputs that are a second handle on another device, not a microphone.
_ALIAS_INPUTS = ("microsoft sound mapper", "primary sound capture")
# Inputs that record what the speakers play. Never a backup: rescuing a silent
# mic with one would paste the video that was playing as if it were dictated.
_LOOPBACK_INPUTS = ("stereo mix", "what u hear", "wave out mix", "loopback")
_MAX_BACKUPS = 2

# PortAudio is one process-wide library; opening streams and re-reading the
# device list must not interleave.
_PA_LOCK = threading.Lock()


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    device: int | None = None
    vad_enabled: bool = True
    silence_timeout_ms: int = 1500
    # With no device pinned, also listen on the other real microphones and use
    # one of them when the system default hears nothing.
    fallback_mics: bool = True


@dataclass
class Take:
    """What one microphone heard during a recording."""
    device: str
    is_default: bool
    audio: np.ndarray
    rms: float


@dataclass
class CaptureInfo:
    """Where the last recording came from, for the log and the user."""
    device: str
    rescued_from: str | None   # the silent default this take replaced
    levels: dict[str, float]


def rms(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))


def choose_take(takes: list[Take], gate: float = SILENCE_RMS) -> Take:
    """Pick the recording to transcribe. takes[0] is the system default.

    The default wins whenever it heard anything, because it is the device the
    user chose in Windows. A backup is used only to rescue a silent default.
    """
    default = takes[0]
    if default.rms >= gate:
        return default
    heard = [t for t in takes[1:] if t.rms >= gate]
    if heard:
        return max(heard, key=lambda t: t.rms)
    return default


def _is_real_mic(name: str) -> bool:
    low = name.lower()
    return not any(tag in low for tag in _ALIAS_INPUTS + _LOOPBACK_INPUTS)


def _host_inputs(sd) -> tuple[int, list[tuple[int, str]]]:
    """(default input index, inputs sharing its host API). Aliases excluded."""
    default = sd.default.device[0]
    devices = list(sd.query_devices())
    if default is None or not 0 <= default < len(devices):
        return -1, []
    api = devices[default]["hostapi"]
    return default, [
        (i, d["name"]) for i, d in enumerate(devices)
        if d["hostapi"] == api and d["max_input_channels"] > 0
        and not any(tag in d["name"].lower() for tag in _ALIAS_INPUTS)
    ]


def backup_inputs(sd) -> list[tuple[int, str]]:
    """Other real microphones on the default input's host API, as (index, name).

    Staying on one host API keeps each physical mic to a single entry; the same
    hardware is listed again under DirectSound, WASAPI and WDM-KS.
    """
    try:
        default, inputs = _host_inputs(sd)
    except Exception as e:
        _log.debug("could not list input devices: %s", e)
        return []
    seen = {name for i, name in inputs if i == default}
    out = []
    for i, name in inputs:
        if name in seen or not _is_real_mic(name):
            continue
        seen.add(name)
        out.append((i, name))
    return out


def _device_name(sd, device: int | None) -> str:
    try:
        index = sd.default.device[0] if device is None else device
        return str(sd.query_devices(index)["name"])
    except Exception:
        return "default input" if device is None else f"device {device}"


def _system_input_count() -> int | None:
    """How many capture devices Windows has right now; None when unknown."""
    try:
        import ctypes
        return int(ctypes.windll.winmm.waveInGetNumDevs())
    except Exception:
        return None


def _refresh_if_stale(sd) -> None:
    """Re-read the device list when a microphone was plugged in or removed.

    PortAudio snapshots devices when it initializes. After a hot plug the
    snapshot's indexes can name a different device and a new microphone is
    invisible, so a daemon started before the plug would record from the wrong
    one until restarted. Counting capture devices costs microseconds, so this
    runs on every press and only pays for a re-read when the count moved.
    """
    if not (hasattr(sd, "_terminate") and hasattr(sd, "_initialize")):
        return
    try:
        default, inputs = _host_inputs(sd)
        if default < 0 or sd.query_hostapis(sd.query_devices(default)["hostapi"])["name"] != "MME":
            return
        now = _system_input_count()
        if now is None or now == len(inputs):
            return
        _log.info("input devices changed (%d -> %d), re-reading the list", len(inputs), now)
        sd._terminate()
        sd._initialize()
    except Exception as e:
        _log.warning("could not refresh the input device list: %s", e)


class _Tap:
    """A backup microphone: one open stream and what it has heard."""

    def __init__(self, name: str):
        self.name = name
        self.stream = None
        self.chunks: list[np.ndarray] = []

    def callback(self, indata, frames, time_info, status):
        self.chunks.append(indata.copy())

    def audio(self) -> np.ndarray:
        return _flatten(self.chunks)


def _flatten(chunks: list[np.ndarray]) -> np.ndarray:
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks, axis=0).flatten().astype(np.float32)


class Recorder:
    """Streaming recorder. start() begins capture; stop() returns float32 mono PCM."""

    # Class-level so a recorder built without __init__ (tests) still works.
    _backups: list[_Tap] = []
    _primary_name: str = "default input"
    last_capture: CaptureInfo | None = None

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
        with _PA_LOCK:
            _refresh_if_stale(sd)
            self._q = queue.Queue()
            self._stream = self._open(sd, self.cfg.device, self._callback)
            # try/except: InputStream(...) already opened the device, so if
            # start() raises (exclusive-mode conflict, sample-rate mismatch) we
            # must close the handle ourselves, sounddevice's stream defines no
            # __del__, so a dropped reference orphans the device and its
            # callback thread for the life of the process.
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
            self._primary_name = _device_name(sd, self.cfg.device)
            # The default is already capturing, so backups opening a few tens
            # of milliseconds later cost it nothing.
            self._backups = self._open_backups(sd)

    def _open(self, sd, device, callback):
        return sd.InputStream(
            samplerate=self.cfg.sample_rate,
            channels=self.cfg.channels,
            device=device,
            dtype="float32",
            callback=callback,
            blocksize=int(self.cfg.sample_rate * 0.03),  # 30ms
        )

    def _open_backups(self, sd) -> list[_Tap]:
        if self.cfg.device is not None or not self.cfg.fallback_mics:
            return []
        taps = []
        for index, name in backup_inputs(sd)[:_MAX_BACKUPS]:
            tap = _Tap(name)
            try:
                tap.stream = self._open(sd, index, tap.callback)
                tap.stream.start()
            except Exception as e:
                _log.warning("backup microphone %s did not open: %s", name, e)
                self._close(tap.stream)
                continue
            taps.append(tap)
        return taps

    @staticmethod
    def _close(stream) -> None:
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception as e:
            _log.warning("audio stream stop/close failed: %s", e)

    def stop(self) -> np.ndarray:
        return self._finish([])

    def _finish(self, head: list[np.ndarray]) -> np.ndarray:
        """Close every stream and return the take worth transcribing. `head`
        is default-mic audio a caller already drained from the queue."""
        if not self._recording:
            return np.zeros(0, dtype=np.float32)
        # try/finally: if _stream.stop() raises (e.g. PortAudio error on device
        # removal), we must still close the stream and clear _recording —
        # otherwise the handle leaks AND start() early-returns forever, wedging
        # the recorder until restart.
        backups, self._backups = self._backups, []
        # Each close blocks for tens of milliseconds and this is the
        # release-to-text path, so the backups close while the default does.
        closers = [threading.Thread(target=self._close, args=(tap.stream,))
                   for tap in backups]
        for t in closers:
            t.start()
        try:
            self._close(self._stream)
        finally:
            self._stream = None
            self._recording = False
            for t in closers:
                t.join()
        chunks = list(head)
        while not self._q.empty():
            chunks.append(self._q.get())
        default = _flatten(chunks)
        takes = [Take(self._primary_name, True, default, rms(default))]
        takes += [Take(t.name, False, a, rms(a)) for t in backups for a in [t.audio()]]
        chosen = choose_take(takes)
        self.last_capture = CaptureInfo(
            device=chosen.device,
            rescued_from=None if chosen.is_default else self._primary_name,
            levels={t.device: t.rms for t in takes},
        )
        return chosen.audio

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
            # A backup counts too: with a dead default mic, judging silence on
            # the default alone would cut the user off after the timeout.
            voiced = self._is_voiced(sample, vad) or any(
                self._is_voiced(_flatten(tap.chunks[-10:]), vad)
                for tap in self._backups if tap.chunks)
            if voiced:
                last_voice_t = time.time()
            elif (time.time() - last_voice_t) * 1000 > self.cfg.silence_timeout_ms:
                break
        # Stop the streams; chunks that arrived after the last drain are still
        # in the queue and join `collected` there.
        return self._finish(collected)

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
