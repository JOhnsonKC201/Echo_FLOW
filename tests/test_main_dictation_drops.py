"""Every way _do_dictation can drop a recording must leave a trace in wispr.log.

The daemon runs detached with stdout/stderr sent to DEVNULL (src/watchdog.py),
so console.print is invisible and an exception on the dictation thread vanishes.
From 2026-09-17 every dictation ended at "hotkey released: stop, captured N
samples" with nothing after it, and the log could not say why.
"""
from __future__ import annotations

import logging
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest

from src import main as main_mod
from src.main import App


def _make_app():
    app = App.__new__(App)
    app._paused = False
    app.cfg = {"audio": {"sample_rate": 16000}, "experimental": {}}
    app.tray = MagicMock()
    app._pipeline_lock = threading.Lock()
    app.transcriber = MagicMock()
    return app


@pytest.fixture
def toasts(monkeypatch):
    # src/log.py stops "wispr" propagating to root, which is where caplog listens.
    monkeypatch.setattr(logging.getLogger("wispr"), "propagate", True)
    sent = MagicMock()
    monkeypatch.setattr(main_mod.wnotify, "notify", sent)
    return sent


def test_quiet_clip_is_logged_and_toasted(caplog, toasts):
    app = _make_app()
    quiet = np.full(16000, 0.0005, dtype=np.float32)  # 1s, RMS 0.0005

    with caplog.at_level(logging.INFO, logger="wispr.main"):
        app._do_dictation(quiet)

    assert "too quiet" in caplog.text
    assert "0.0005" in caplog.text
    toasts.assert_called_once()
    app.transcriber.transcribe.assert_not_called()


def test_short_clip_is_logged(caplog, toasts):
    app = _make_app()
    short = np.full(3200, 0.1, dtype=np.float32)  # 200ms

    with caplog.at_level(logging.INFO, logger="wispr.main"):
        app._do_dictation(short)

    assert "too short" in caplog.text
    app.transcriber.transcribe.assert_not_called()


def test_worker_exception_is_logged_and_tray_recovers(caplog, toasts):
    app = _make_app()
    app.transcriber.transcribe.side_effect = RuntimeError("cuda went away")
    loud = np.full(16000, 0.1, dtype=np.float32)

    with caplog.at_level(logging.INFO, logger="wispr.main"):
        app._dictation_worker(loud, None)  # must not raise

    assert "dictation failed" in caplog.text
    assert "cuda went away" in caplog.text
    app.tray.set_state.assert_called_with("ok")
    toasts.assert_called_once()


def _capture(device, rescued_from, **levels):
    from src.audio import CaptureInfo
    return CaptureInfo(device=device, rescued_from=rescued_from, levels=levels)


def test_quiet_drop_names_every_microphone_and_its_level(caplog, toasts):
    """Three weeks of "too quiet" drops could not be explained because the log
    never said which device the silence came from."""
    app = _make_app()
    quiet = np.full(16000, 0.0005, dtype=np.float32)
    cap = _capture("USB mic", None, **{"USB mic": 0.0005, "Laptop array": 0.0002})

    with caplog.at_level(logging.INFO, logger="wispr.main"):
        app._do_dictation(quiet, capture=cap)

    assert "USB mic 0.0005" in caplog.text
    assert "Laptop array 0.0002" in caplog.text
    assert "USB mic" in toasts.call_args.args[1]


def test_a_rescued_recording_is_logged_every_time_and_toasted_once(caplog, toasts):
    app = _make_app()
    app.recorder = MagicMock()
    app.recorder.last_capture = _capture(
        "Laptop array", "USB mic", **{"USB mic": 0.0005, "Laptop array": 0.05})

    with caplog.at_level(logging.INFO, logger="wispr.main"):
        first = app._note_capture()
        app._note_capture()

    assert first.device == "Laptop array"
    assert caplog.text.count("default microphone USB mic was silent") == 2
    toasts.assert_called_once()
    assert "USB mic heard nothing" in toasts.call_args.args[1]


def test_an_ordinary_recording_reports_nothing(caplog, toasts):
    app = _make_app()
    app.recorder = MagicMock()
    app.recorder.last_capture = _capture("USB mic", None, **{"USB mic": 0.05})

    with caplog.at_level(logging.INFO, logger="wispr.main"):
        app._note_capture()

    assert "silent" not in caplog.text
    toasts.assert_not_called()
