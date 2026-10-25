"""Warm the models while the user talks.

After a break the cleanup model has been unloaded (cleanup.ollama.keep_alive)
and the GPU has dropped to an idle power state, so the first dictation paid a
~2 s model load plus a slow encode AFTER the hotkey was released. Measured on
the last 150 dictations: cleanup p50 0.36 s with a gap under 10 min, 3.8 s
after an hour. Pressing the hotkey now starts both warm-ups in the background,
so the load overlaps the seconds spent speaking."""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

import src.main as main_mod
from src.main import App
from tests.test_main_state_race import _make_app

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr("src.main.wsound.play", lambda *a, **k: None)
    monkeypatch.setattr("src.main.console.print", lambda *a, **k: None)


def _app(idle_s: float):
    app = _make_app()
    app._pipeline_lock = threading.RLock()
    app.cleaner = MagicMock()
    app.transcriber = MagicMock()
    app._last_model_use = time.monotonic() - idle_s
    return app


def _wait_for(pred, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def test_press_after_a_break_warms_both_models():
    app = _app(idle_s=main_mod.PREWARM_IDLE_S + 60)
    app.on_press_hold()
    assert _wait_for(lambda: app.transcriber.warm.called)
    app.cleaner.warmup.assert_called_once()
    app.recorder.start.assert_called_once()


def test_press_soon_after_a_dictation_does_not_warm():
    app = _app(idle_s=30)
    app.on_press_hold()
    time.sleep(0.1)
    app.cleaner.warmup.assert_not_called()
    app.transcriber.warm.assert_not_called()


def test_toggle_mode_press_warms_too(monkeypatch):
    app = _app(idle_s=main_mod.PREWARM_IDLE_S + 60)
    app._cancelled = False
    monkeypatch.setattr(app, "_maybe_prewarm", MagicMock(), raising=False)
    # Stop the toggle recording thread from doing real work.
    app.recorder.record_until_silence = MagicMock(return_value=np.zeros(0, np.float32))
    app.on_toggle()
    app._maybe_prewarm.assert_called_once()


def test_only_one_warm_up_runs_at_a_time():
    app = _app(idle_s=main_mod.PREWARM_IDLE_S + 60)
    release = threading.Event()
    app.cleaner.warmup.side_effect = lambda: release.wait(2)
    app._maybe_prewarm()
    app._last_model_use = time.monotonic() - main_mod.PREWARM_IDLE_S - 60
    app._maybe_prewarm()
    release.set()
    assert _wait_for(lambda: app.transcriber.warm.called)
    assert app.cleaner.warmup.call_count == 1


def test_whisper_warm_skips_when_a_dictation_holds_the_model():
    """Never queue a warm encode in front of a real transcription."""
    app = _app(idle_s=main_mod.PREWARM_IDLE_S + 60)
    held = threading.Event()
    done = threading.Event()

    def hold():
        with app._pipeline_lock:
            held.set()
            done.wait(2)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(2)
    app._maybe_prewarm()
    assert _wait_for(lambda: app.cleaner.warmup.called)
    time.sleep(0.1)
    done.set()
    t.join(2)
    app.transcriber.warm.assert_not_called()


def test_a_failing_warm_up_never_breaks_recording():
    app = _app(idle_s=main_mod.PREWARM_IDLE_S + 60)
    app.cleaner.warmup.side_effect = RuntimeError("ollama down")
    app.transcriber.warm.side_effect = RuntimeError("cuda gone")
    app.on_press_hold()
    app.recorder.start.assert_called_once()
    time.sleep(0.1)
    # A later press after the failure may try again.
    app._last_model_use = time.monotonic() - main_mod.PREWARM_IDLE_S - 60
    app._maybe_prewarm()
    assert _wait_for(lambda: app.cleaner.warmup.call_count == 2)


def test_app_shell_without_models_is_a_no_op():
    app = App.__new__(App)
    app._maybe_prewarm()        # must not raise


def test_transcriber_warm_runs_one_encode_without_vad():
    from src.transcribe import Transcriber
    t = Transcriber.__new__(Transcriber)
    t.resolved_device = "cuda"
    t.model = MagicMock()
    t.model.transcribe.return_value = (iter([]), None)
    assert t.warm() is True
    kwargs = t.model.transcribe.call_args.kwargs
    assert kwargs["vad_filter"] is False


def test_transcriber_warm_never_raises():
    from src.transcribe import Transcriber
    t = Transcriber.__new__(Transcriber)
    t.resolved_device = "cuda"
    t.model = MagicMock()
    t.model.transcribe.side_effect = RuntimeError("cublas64_12.dll not found")
    assert t.warm() is False
