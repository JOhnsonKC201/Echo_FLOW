"""Back-to-back dictations through the real _do_dictation pipeline with fake
models. A long dictation followed quickly by short ones is the realistic
burst: the later ones finish recording while the first is still being
transcribed, and all of them queue on the shared model lock.

This is a smoke test for loss, duplication and leftover threads under a
burst. The order assertion only fails on an unlucky interleaving (about 1 in
180 runs before the fix); the deterministic order guard is
tests/test_main_paste_order.py."""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from src.history import History
from tests.test_main_humanize import _cfg, _make_app

pytestmark = pytest.mark.load

N = 20
SR = 16000


def _burst_app(tmp_path, monkeypatch):
    monkeypatch.setattr("src.main.console.print", lambda *a, **k: None)
    monkeypatch.setattr("src.main.wnotify.notify", lambda *a, **k: None)
    monkeypatch.setattr("src.main.wsound.play", lambda *a, **k: None)
    app = _make_app(_cfg(), History(str(tmp_path / "h.db")))

    def transcribe(audio, sr):
        # The dictation number rides in the first sample; longer clips take
        # longer, so dictation 0 (the longest) is the slowest to transcribe.
        idx = int(round(float(audio[0]) * 1000))
        time.sleep(len(audio) / SR * 0.02)
        return f"dictation number {idx}", "en", {}

    app.transcriber.transcribe.side_effect = transcribe
    app.cleaner.clean.side_effect = lambda raw, **k: (time.sleep(0.01) or (raw, False))
    pasted, lock = [], threading.Lock()

    def inject(text):
        with lock:
            pasted.append(text.strip())

    app.injector.inject.side_effect = inject
    return app, pasted


def _audio(idx: int, seconds: float) -> np.ndarray:
    a = np.full(int(SR * seconds), 0.1, dtype=np.float32)
    a[0] = idx / 1000
    return a


def test_burst_pastes_every_dictation_once_in_release_order(tmp_path, monkeypatch):
    app, pasted = _burst_app(tmp_path, monkeypatch)
    before = threading.active_count()

    threads = []
    for i in range(N):
        seconds = 6.0 if i == 0 else 0.6    # one long dictation, then quick ones
        t = threading.Thread(target=app._dictation_worker,
                             args=(_audio(i, seconds), time.perf_counter()))
        t.start()
        threads.append(t)
        time.sleep(0.005)                   # releases arrive in order
    for t in threads:
        t.join(30)

    assert not any(t.is_alive() for t in threads)
    assert threading.active_count() <= before + 2   # no stragglers left behind
    expected = [f"dictation number {i}" for i in range(N)]
    assert sorted(pasted) == sorted(expected), "a dictation was lost or doubled"
    assert pasted == expected, "pastes arrived out of the order they were spoken"
