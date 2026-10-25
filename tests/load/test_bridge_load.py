"""100 phones hitting the mobile bridge at once, over real sockets.

The bridge admits one job at a time on purpose (the GPU is shared with the
desktop hotkey), so most of a burst SHOULD be turned away. What must hold:
every answer is a clean 200 / 401 / 429 / 503, nothing hangs or 500s, the
models never run two jobs at once, and every accepted dictation is stored
exactly once."""
from __future__ import annotations

import threading
import time

import pytest
import requests

from src import bridge
from tests.load.conftest import hammer
from tests.test_bridge import _make_app_ref, _StubTranscriber, _wav_bytes

pytestmark = pytest.mark.load

KEY = "load-test-key-0123456789"
WAV = _wav_bytes(duration_s=1.0)


class _CountingTranscriber(_StubTranscriber):
    """Records the most jobs that were ever inside transcribe() together."""

    def __init__(self, delay: float):
        super().__init__(delay=delay)
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def transcribe(self, audio, sample_rate=16000):
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            return super().transcribe(audio, sample_rate)
        finally:
            with self._lock:
                self.active -= 1


@pytest.fixture
def bridge_app(temp_db, monkeypatch):
    # The brute-force tables are module-level (one bridge per process), so a
    # lockout from one test would otherwise follow 127.0.0.1 into the next.
    monkeypatch.setattr(bridge, "_auth_failures", {})
    monkeypatch.setattr(bridge, "_auth_lockouts", {})
    h, _ = temp_db
    app_ref = _make_app_ref(history=h)
    app_ref.transcriber = _CountingTranscriber(delay=0.05)
    flask_app = bridge._make_app(app_ref, shared_key=KEY, default_style="casual",
                                 allow_history_write=True)
    return flask_app, app_ref


def _dictate(base, key=KEY):
    return requests.post(base + "/v1/dictate", data=WAV, timeout=30,
                         headers={"X-Echo-Key": key,
                                  "Content-Type": "audio/wav"}).status_code


def test_100_phones_dictating_at_once(serve, bridge_app):
    flask_app, app_ref = bridge_app
    base = serve(flask_app)
    report = hammer(lambda i: _dictate(base))
    print(report.summary())

    assert report.errors == []
    assert set(report.statuses) <= {200, 429, 503}, report.statuses
    assert report.statuses.get(200, 0) >= 1
    assert app_ref.transcriber.peak == 1
    assert report.percentile(1.0) < 10.0, report.summary()
    stored = app_ref.history.conn.execute(
        "SELECT COUNT(*) FROM dictations WHERE source = 'mobile'").fetchone()[0]
    assert stored == report.statuses[200]


def test_retrying_phones_all_get_through(serve, bridge_app):
    """A phone that honours Retry-After eventually lands its dictation."""
    flask_app, app_ref = bridge_app
    base = serve(flask_app)

    def with_retry(i):
        for _ in range(200):
            status = _dictate(base)
            if status == 200:
                return 200
            assert status in (429, 503)
            time.sleep(0.05)
        return status

    report = hammer(with_retry, n=20)
    print(report.summary())
    assert report.statuses == {200: 20}
    assert app_ref.transcriber.peak == 1


def test_100_wrong_keys_trip_the_lockout_cleanly(serve, bridge_app):
    flask_app, _ = bridge_app
    base = serve(flask_app)
    report = hammer(lambda i: _dictate(base, key=f"wrong-{i}"))
    print(report.summary())

    assert report.errors == []
    assert set(report.statuses) <= {401, 429}, report.statuses
    # The lockout engages at the threshold; a burst cannot buy extra guesses.
    assert report.statuses.get(401, 0) <= bridge._AUTH_FAIL_THRESHOLD


def test_history_reads_during_a_dictation_burst(serve, bridge_app):
    flask_app, _ = bridge_app
    base = serve(flask_app)

    def mixed(i):
        if i % 2:
            return requests.get(base + "/v1/history?limit=50", timeout=30,
                                headers={"X-Echo-Key": KEY}).status_code
        return _dictate(base)

    report = hammer(mixed)
    print(report.summary())
    assert report.errors == []
    assert set(report.statuses) <= {200, 429, 503}, report.statuses
    # Reads never queue behind the model: all 50 history reads succeed.
    assert report.statuses[200] >= 50
