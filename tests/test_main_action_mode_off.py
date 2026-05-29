"""Phase 14 — Action Mode integration in main._do_dictation.

Proves the safety-critical wiring without standing up a real App:
  - flag OFF  → behaviour is byte-identical (text still pastes; nothing fires)
  - flag ON   → a recognised action fires and leaves NO paste behind, and is
                logged to voice_actions
  - no prefix → a plain dictation can never trigger an action
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import numpy as np
import pytest

from src.main import App


def _make_app(cfg, transcript, history=None):
    """A bare App with just the attributes _do_dictation touches up to inject.
    Transcriber/cleaner/injector are mocked; the cleaned text == transcript."""
    app = App.__new__(App)
    app._paused = False
    app.cfg = cfg
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

    app.transcriber = MagicMock()
    app.transcriber.transcribe.return_value = (transcript, "en", {})
    app.cleaner = MagicMock()
    app.cleaner.pick_style.return_value = "default"
    app.cleaner.clean.return_value = (transcript, False)
    app.injector = MagicMock()
    app.injector.trailing_space = True
    return app


def _audio():
    # 1s of non-silent float32 audio: passes the >400ms and RMS>0.003 gates.
    return np.ones(16000, dtype=np.float32) * 0.1


def _base_cfg(**experimental):
    return {"audio": {"sample_rate": 16000}, "experimental": experimental}


def test_action_mode_off_pastes_text():
    cfg = _base_cfg(command_mode=False, action_mode=False, command_prefix="computer")
    app = _make_app(cfg, "computer open spotify")
    app._do_dictation(_audio())
    # Action Mode inert → the dictation is pasted exactly as before.
    app.injector.inject.assert_called_once()
    assert app.injector.inject.call_args.args[0] == "computer open spotify"


def test_action_mode_on_fires_and_suppresses_paste(temp_db, monkeypatch):
    history, _path = temp_db
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda u, **k: opened.append(u) or True)
    monkeypatch.setattr("src.notify.notify", lambda *a, **k: None)

    cfg = _base_cfg(command_mode=False, action_mode=True, command_prefix="computer")
    app = _make_app(cfg, "computer search the web for cats", history=history)
    app._do_dictation(_audio())

    # The action fired into the browser and NOTHING was pasted.
    assert opened == ["https://www.google.com/search?q=cats"]
    app.injector.inject.assert_not_called()

    # ...and it was logged to voice_actions.
    rows = history.recent_actions()
    assert len(rows) == 1
    assert rows[0]["handler"] == "web_search"
    assert rows[0]["ok"] is True


def test_plain_dictation_never_triggers_action(monkeypatch):
    called = []
    monkeypatch.setattr("webbrowser.open", lambda u, **k: called.append(u) or True)
    monkeypatch.setattr("src.notify.notify", lambda *a, **k: None)

    cfg = _base_cfg(command_mode=False, action_mode=True, command_prefix="computer")
    app = _make_app(cfg, "open the spotify app and play something")
    app._do_dictation(_audio())

    # No prefix → no action, just a normal paste.
    assert called == []
    app.injector.inject.assert_called_once()
    assert app.injector.inject.call_args.args[0] == "open the spotify app and play something"


def test_command_mode_runs_before_action(monkeypatch):
    # Both modes on; a real Command Mode hit ("go to the top" → Ctrl+Home) must
    # fire the keystroke and NOT fall through to Action Mode.
    monkeypatch.setattr("src.notify.notify", lambda *a, **k: None)
    cfg = _base_cfg(command_mode=True, action_mode=True, command_prefix="computer")
    app = _make_app(cfg, "computer go to the top")
    app.injector.send_hotkey.return_value = True
    app._do_dictation(_audio())
    app.injector.send_hotkey.assert_called_once_with("ctrl+home")
    app.injector.inject.assert_not_called()


def test_command_miss_falls_through_to_action(temp_db, monkeypatch):
    # "go to github.com" isn't a Command Mode keystroke → falls through to
    # Action Mode, which opens the URL. One action, no paste.
    history, _path = temp_db
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda u, **k: opened.append(u) or True)
    monkeypatch.setattr("src.notify.notify", lambda *a, **k: None)
    cfg = _base_cfg(command_mode=True, action_mode=True, command_prefix="computer")
    app = _make_app(cfg, "computer go to github.com", history=history)
    app._do_dictation(_audio())
    assert opened == ["https://github.com"]
    app.injector.inject.assert_not_called()
    rows = history.recent_actions()
    assert rows and rows[0]["handler"] == "open_url"
    # SEC-3: the logged URL arg is reduced to scheme+host (here already host-only).
    assert "github.com" in (rows[0]["args"] or "")
