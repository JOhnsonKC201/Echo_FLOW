"""Regression: Recorder.stop() must not leak the stream or wedge the recorder
if the underlying stream.stop() raises (e.g. PortAudio error on device removal).
Before the try/finally, _recording stayed True and start() early-returned
forever."""
from __future__ import annotations

import numpy as np


class _BoomStream:
    def stop(self):
        raise RuntimeError("PortAudio: device unplugged")
    def close(self):
        raise RuntimeError("should not matter")


def _make_recorder():
    from src.audio import Recorder, AudioConfig
    # vad_enabled=False so __init__ doesn't try to load silero.
    return Recorder(AudioConfig(vad_enabled=False))


def test_stop_resets_state_even_if_stream_raises():
    rec = _make_recorder()
    rec._recording = True
    rec._stream = _BoomStream()
    out = rec.stop()  # must not raise
    assert isinstance(out, np.ndarray)
    assert rec._recording is False
    assert rec._stream is None


def test_stop_when_not_recording_is_noop():
    rec = _make_recorder()
    out = rec.stop()
    assert isinstance(out, np.ndarray)
    assert out.size == 0
