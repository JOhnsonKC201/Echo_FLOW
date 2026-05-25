"""Desktop hotkey vs. mobile bridge contention on the shared _pipeline_lock.

Computer-first contract: a slow bridge request must NEVER block the desktop
hot path forever. The bridge tries _PIPELINE_LOCK_TIMEOUT_S (2.0s) to acquire
the lock; past that the phone gets a 503 + Retry-After, not a hung connection.
"""
from __future__ import annotations

import io
import threading
import time
import wave

import numpy as np
import pytest

from tests.test_bridge import (
    _make_app_ref,
    _wav_bytes,
)


def _build_client(temp_db):
    from src import bridge
    h, _ = temp_db
    app_ref = _make_app_ref(history=h)
    flask_app = bridge._make_app(app_ref, "test-key", "casual", True)
    flask_app.config["TESTING"] = True
    return flask_app.test_client(), app_ref


def test_bridge_waits_for_desktop_lock_within_timeout(temp_db):
    """Desktop holds the lock for ~1s; bridge waits and succeeds (timeout=2s)."""
    client, app_ref = _build_client(temp_db)
    lock = app_ref._pipeline_lock
    wav = _wav_bytes(sr=16000, duration_s=1.0)

    holder_done = threading.Event()

    def hold_lock():
        with lock:
            time.sleep(1.0)
        holder_done.set()

    t = threading.Thread(target=hold_lock, daemon=True)
    t.start()
    # Make sure the holder grabbed the lock before we fire the request.
    time.sleep(0.05)

    t0 = time.time()
    r = client.post("/v1/dictate",
                    data={"file": (io.BytesIO(wav), "audio.wav")},
                    headers={"X-Echo-Key": "test-key"},
                    content_type="multipart/form-data")
    elapsed = time.time() - t0
    t.join(timeout=2.0)

    assert r.status_code == 200, f"expected 200 (bridge waited), got {r.status_code}"
    assert holder_done.is_set()
    # The bridge waited at least ~0.9s for the desktop lock.
    assert elapsed >= 0.8, f"bridge returned suspiciously fast ({elapsed:.2f}s)"


def test_bridge_times_out_when_desktop_holds_lock_too_long(temp_db):
    """Desktop holds lock for 3s; bridge gives up at 2s with 503 + Retry-After: 1."""
    client, app_ref = _build_client(temp_db)
    lock = app_ref._pipeline_lock
    wav = _wav_bytes(sr=16000, duration_s=1.0)

    stop_holder = threading.Event()

    def hold_lock():
        with lock:
            # Hold for 3s OR until told to release (cleanup safety).
            stop_holder.wait(timeout=3.0)

    t = threading.Thread(target=hold_lock, daemon=True)
    t.start()
    time.sleep(0.05)

    t0 = time.time()
    r = client.post("/v1/dictate",
                    data={"file": (io.BytesIO(wav), "audio.wav")},
                    headers={"X-Echo-Key": "test-key"},
                    content_type="multipart/form-data")
    elapsed = time.time() - t0

    # Release the holder so the next test isn't poisoned.
    stop_holder.set()
    t.join(timeout=3.5)

    assert r.status_code == 503, f"expected 503 on lock timeout, got {r.status_code}: {r.data!r}"
    assert r.headers.get("Retry-After") == "1"
    # Should have given up around the 2.0s budget, not waited the full 3s.
    assert 1.8 <= elapsed <= 2.8, f"unexpected lock-wait duration: {elapsed:.2f}s"
