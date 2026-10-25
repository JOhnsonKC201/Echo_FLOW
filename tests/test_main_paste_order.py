"""Dictations paste in the order they were spoken.

The model lock is taken twice per dictation (transcribe, then clean) and the
paste happens after both, outside it. So when dictation A is slow between its
two sections, a later dictation B can take the lock for its own cleanup first
and paste before A. Found by tests/load/test_dictation_burst.py; this test
forces that interleaving deterministically."""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from src.history import History
from tests.test_main_humanize import _cfg, _make_app

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr("src.main.console.print", lambda *a, **k: None)
    monkeypatch.setattr("src.main.wnotify.notify", lambda *a, **k: None)
    app = _make_app(_cfg(), History(str(tmp_path / "h.db")))
    app._pipeline_lock = threading.RLock()
    app.transcriber.transcribe.side_effect = (
        lambda audio, sr: (f"dictation {int(audio[0])}", "en", {}))
    app.cleaner.clean.side_effect = lambda raw, **k: (raw, False)
    app.pasted = []
    app.injector.inject.side_effect = lambda text: app.pasted.append(text.strip())
    return app


def _audio(n):
    a = np.full(16000, 0.1, dtype=np.float32)
    a[0] = n
    return a


def _slow_first_style_pick(app, seconds):
    calls = []

    def pick_style(title):
        calls.append(title)
        if len(calls) == 1:
            time.sleep(seconds)      # A stalls between transcribe and clean
        return "default"

    app.cleaner.pick_style.side_effect = pick_style


def test_a_later_dictation_cannot_overtake_an_earlier_one(app):
    _slow_first_style_pick(app, 0.3)
    a = threading.Thread(target=app._dictation_worker, args=(_audio(1), time.perf_counter()))
    a.start()
    time.sleep(0.05)
    b = threading.Thread(target=app._dictation_worker, args=(_audio(2), time.perf_counter()))
    b.start()
    a.join(10)
    b.join(10)
    assert app.pasted == ["dictation 1", "dictation 2"]


def test_a_dropped_dictation_does_not_hold_up_the_next(app):
    """An earlier dictation that never pastes (empty transcript) must release
    its turn, or every later paste would wait forever."""
    app.transcriber.transcribe.side_effect = lambda audio, sr: (
        ("", "en", {}) if int(audio[0]) == 1 else (f"dictation {int(audio[0])}", "en", {}))
    a = threading.Thread(target=app._dictation_worker, args=(_audio(1), time.perf_counter()))
    b = threading.Thread(target=app._dictation_worker, args=(_audio(2), time.perf_counter()))
    a.start()
    time.sleep(0.05)
    t0 = time.perf_counter()
    b.start()
    a.join(10)
    b.join(10)
    assert app.pasted == ["dictation 2"]
    assert time.perf_counter() - t0 < 2.0


def test_a_crashed_dictation_does_not_hold_up_the_next(app):
    def transcribe(audio, sr):
        if int(audio[0]) == 1:
            raise RuntimeError("cuda went away")
        return f"dictation {int(audio[0])}", "en", {}

    app.transcriber.transcribe.side_effect = transcribe
    a = threading.Thread(target=app._dictation_worker, args=(_audio(1), time.perf_counter()))
    b = threading.Thread(target=app._dictation_worker, args=(_audio(2), time.perf_counter()))
    a.start()
    time.sleep(0.05)
    b.start()
    a.join(10)
    b.join(10)
    assert app.pasted == ["dictation 2"]


def test_direct_do_dictation_calls_still_paste(app):
    """Callers that skip the worker (tests, re-paste paths) are unaffected."""
    app._do_dictation(_audio(7))
    assert app.pasted == ["dictation 7"]
