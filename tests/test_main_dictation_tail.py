"""The post-cleanup tail of App._do_dictation (inject → history → notify).

Covers the stage AFTER transcription+cleanup succeed:
  - happy path: the CLEANED text is injected, a history row is persisted
    (with the quality score from the async grader), and the in-memory
    re-paste cache is primed
  - injection failure: pins the REAL current behavior — the exception
    propagates out of _do_dictation (it runs on a daemon thread in prod),
    the history row is NOT written, but the text survives in
    _last_cleaned_text so the Ctrl+Win re-paste recovers it
  - history failure: the async logger swallows the error — injection has
    already happened and nothing propagates to the hotkey thread

The background threads (_log_async / _post_process) are made synchronous by
patching threading.Thread with an inline runner, so every assertion is
deterministic — no sleeps, no polling.
"""
from __future__ import annotations

import threading
import types
from unittest.mock import MagicMock

import numpy as np
import pytest

from src.main import App

RAW = "hello there this is johnson dictating a quick test sentence"
CLEANED = "Hello there, this is Johnson dictating a quick test sentence."


def _audio():
    # 1s of non-silent float32 audio: passes the >400ms and RMS>0.003 gates.
    return np.ones(16000, dtype=np.float32) * 0.1


def _make_app(history, raw=RAW, cleaned=CLEANED):
    """Bare App with every attribute the full _do_dictation path touches,
    including the _log_async/_post_process tail (which the action-mode tests
    never reach because they return before inject)."""
    app = App.__new__(App)
    app._paused = False
    app.cfg = {"audio": {"sample_rate": 16000}, "experimental": {}}
    app.tray = None
    app._press_title = "Editor"
    app._pe_cfg = {}
    app._prompt_mode = False
    app._prompt_oneshot = False
    app._armed_transform = None
    app._pipeline_lock = threading.Lock()
    app.learner = None
    app.retriever = None
    app.pattern_miner = None
    app.history = history
    app._scratchpad_target_id = None
    app._last_cleaned_text = None
    # Tail-stage state read/written by _log_async:
    app._grading_weights = None
    app._last_quality = None
    app._recent_qualities = []
    app._last_row_id = None
    app._spawn_teacher_distillation = MagicMock()  # never hit the cloud

    app.transcriber = MagicMock()
    app.transcriber.transcribe.return_value = (raw, "en", {})
    app.cleaner = MagicMock()
    app.cleaner.pick_style.return_value = "default"
    app.cleaner.clean.return_value = (cleaned, False)
    app.injector = MagicMock()
    app.injector.trailing_space = True
    return app


class _InlineThread:
    """threading.Thread stand-in that runs the target synchronously on
    start() — makes the _log_async/_post_process handoff deterministic."""
    def __init__(self, group=None, target=None, name=None, args=(),
                 kwargs=None, *, daemon=None):
        self._target, self._args, self._kwargs = target, args, kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)

    def join(self, timeout=None):
        return None


@pytest.fixture
def quiet_tail(monkeypatch):
    """Stub the tail collaborators: grader, tag suggester, action-item
    extractor, toast notifier — and make the background threads inline."""
    monkeypatch.setattr(
        "src.main.grade_mod.grade",
        lambda *a, **k: types.SimpleNamespace(overall=88.0, to_json=lambda: "{}"),
    )
    monkeypatch.setattr("src.main.tags_mod.suggest_tags", lambda *a, **k: [])
    monkeypatch.setattr("src.main.actions_mod.extract_action_items", lambda *a, **k: [])
    monkeypatch.setattr("src.notify.notify", lambda *a, **k: None)
    monkeypatch.setattr("src.main.threading.Thread", _InlineThread)


def test_happy_path_injects_cleaned_text_and_persists_history(temp_db, quiet_tail):
    history, _path = temp_db
    app = _make_app(history)

    app._do_dictation(_audio())

    # The CLEANED text (not the raw transcript) was pasted exactly once.
    app.injector.inject.assert_called_once_with(CLEANED)

    # A history row was persisted with both texts and the grader's score.
    row = history.conn.execute(
        "SELECT raw_text, cleaned_text, quality_score, window_title, style "
        "FROM dictations"
    ).fetchall()
    assert len(row) == 1
    assert row[0][0] == RAW
    assert row[0][1] == CLEANED
    assert row[0][2] == 88.0
    assert row[0][3] == "Editor"
    assert row[0][4] == "default"

    # Tail bookkeeping: row id published, re-paste cache primed, quality kept.
    assert app._last_row_id is not None
    assert app._last_cleaned_text == CLEANED
    assert app._recent_qualities == [88.0]


def test_injection_failure_text_survives_for_repaste(temp_db, quiet_tail, monkeypatch):
    """Pins current behavior when the paste itself blows up:
      - the exception escapes _do_dictation (daemon thread in prod, so the
        hotkey listener survives — but see report: the history block below
        the inject is never reached, so the row is silently dropped)
      - the text is NOT lost: it was cached in _last_cleaned_text BEFORE
        injection, so the Ctrl+Win re-paste handler recovers it."""
    history, _path = temp_db
    app = _make_app(history)
    app.injector.inject.side_effect = RuntimeError("paste target vanished")

    with pytest.raises(RuntimeError, match="paste target vanished"):
        app._do_dictation(_audio())

    # Current (buggy-but-pinned) behavior: no history row was written —
    # the async logger lives AFTER the inject call and is never reached.
    count = history.conn.execute("SELECT COUNT(*) FROM dictations").fetchone()[0]
    assert count == 0

    # Recovery path: the cleaned text was cached before the paste attempt...
    assert app._last_cleaned_text == CLEANED

    # ...and _on_paste_last actually pastes it once the injector works again.
    monkeypatch.setattr("src.sound.play", lambda *a, **k: None)
    app._active = False
    app.injector.inject = MagicMock()  # injector recovered
    app._on_paste_last()
    app.injector.inject.assert_called_once_with(CLEANED)


def test_history_failure_injection_done_and_nothing_propagates(quiet_tail):
    """history.log raising (disk full, locked DB) must not break dictation:
    the paste already happened, and _log_async swallows the error so nothing
    reaches the hotkey thread."""
    failing_history = MagicMock()
    failing_history.log.side_effect = RuntimeError("disk full")
    app = _make_app(failing_history)

    # Must not raise even though the (inline) logger thread blew up.
    app._do_dictation(_audio())

    failing_history.log.assert_called_once()           # write was attempted
    app.injector.inject.assert_called_once_with(CLEANED)  # paste happened anyway
    assert app._last_row_id is None                    # no row id was published
    assert app._last_cleaned_text == CLEANED           # re-paste cache still primed
