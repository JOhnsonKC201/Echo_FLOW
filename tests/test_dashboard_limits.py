"""Dashboard resource limits: oversized bodies are refused, and the two routes
that hold a slow resource (the humanize model call, a global hotkey probe) run
one at a time instead of piling up threads behind each other."""
from __future__ import annotations

import threading

import pytest

from src.cleanup import HumanizeOutcome
from src.dashboard import app as dash_app
from tests.test_dashboard_myvoice import HDR, _App, _h

pytestmark = pytest.mark.unit


def _flask(tmp_path, cleaner=None):
    app_ref = _App(_h(tmp_path), cleaner=cleaner)
    return dash_app.make_app(app_ref)


def test_oversized_body_is_refused_with_413(tmp_path):
    client = _flask(tmp_path).test_client()
    big = "x" * (dash_app.MAX_REQUEST_BYTES + 1)
    r = client.post("/myvoice/humanize", headers=HDR, data={"hz_text": big})
    assert r.status_code == 413


class _BlockingCleaner:
    """humanize_text parks until released, so a second request overlaps it."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def humanize_text(self, text, **kwargs):
        self.calls += 1
        self.started.set()
        self.release.wait(5)
        return HumanizeOutcome("done", "ok", [], 1, 1)


def test_second_humanize_while_one_runs_is_told_busy(tmp_path):
    cleaner = _BlockingCleaner()
    flask_app = _flask(tmp_path, cleaner=cleaner)
    first = {}

    def run_first():
        first["r"] = flask_app.test_client().post(
            "/myvoice/humanize", headers=HDR, data={"hz_text": "hello there"})

    t = threading.Thread(target=run_first)
    t.start()
    assert cleaner.started.wait(5)
    second = flask_app.test_client().post(
        "/myvoice/humanize", headers=HDR, data={"hz_text": "second one"})
    cleaner.release.set()
    t.join(5)

    assert cleaner.calls == 1
    assert b"already rewriting" in second.data
    assert b"done" in first["r"].data


def test_humanize_runs_again_after_the_first_finishes(tmp_path):
    cleaner = _BlockingCleaner()
    cleaner.release.set()
    client = _flask(tmp_path, cleaner=cleaner).test_client()
    client.post("/myvoice/humanize", headers=HDR, data={"hz_text": "one"})
    client.post("/myvoice/humanize", headers=HDR, data={"hz_text": "two"})
    assert cleaner.calls == 2


def test_humanize_slot_is_released_when_the_model_raises(tmp_path):
    class _Boom:
        calls = 0

        def humanize_text(self, text, **kwargs):
            _Boom.calls += 1
            raise RuntimeError("model fell over")

    client = _flask(tmp_path, cleaner=_Boom()).test_client()
    client.post("/myvoice/humanize", headers=HDR, data={"hz_text": "one"})
    client.post("/myvoice/humanize", headers=HDR, data={"hz_text": "two"})
    assert _Boom.calls == 2


def test_concurrent_hotkey_probe_is_refused(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()

    class _SlowHotKeys:
        def __init__(self, mapping):
            pass

        def start(self):
            started.set()
            release.wait(5)

        def stop(self):
            pass

    import pynput.keyboard
    monkeypatch.setattr(pynput.keyboard, "GlobalHotKeys", _SlowHotKeys)
    flask_app = _flask(tmp_path)
    first = {}

    def run_first():
        first["r"] = flask_app.test_client().post(
            "/api/hotkey/check", headers=HDR, json={"combo": "<ctrl>+<alt>+x"})

    t = threading.Thread(target=run_first)
    t.start()
    assert started.wait(5)
    second = flask_app.test_client().post(
        "/api/hotkey/check", headers=HDR, json={"combo": "<ctrl>+<alt>+y"})
    release.set()
    t.join(5)

    assert second.status_code == 429
    assert second.get_json()["reason"] == "busy"
    assert first["r"].get_json()["available"] is True
