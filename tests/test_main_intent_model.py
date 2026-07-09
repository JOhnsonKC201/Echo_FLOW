"""Phase 14+ — intent model wired into main._do_dictation.

Proves the safety-critical seam without a real App:
  - flag OFF (default) → a mis-phrased command is NOT recovered (byte-identical
    to today: it becomes an "unknown command", nothing fires)
  - flag ON → a mis-phrased command ("navigate to github.com") is recovered and
    fires through the SAME dispatch/log path as a regex hit
  - 'shadow' → the model's guess is observed but NEVER executed
  - even ON, an unconfigured app can't be launched (allowlist stays authority)
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import numpy as np

from src.main import App


def _make_app(cfg, transcript, history=None):
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
    return np.ones(16000, dtype=np.float32) * 0.1


def _cfg(**experimental):
    experimental.setdefault("command_mode", False)
    experimental.setdefault("action_mode", True)
    experimental.setdefault("command_prefix", "computer")
    return {"audio": {"sample_rate": 16000}, "experimental": experimental}


def test_intent_model_off_by_default_no_recovery(temp_db, monkeypatch):
    history, _ = temp_db
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda u, **k: opened.append(u) or True)
    monkeypatch.setattr("src.notify.notify", lambda *a, **k: None)

    # No action_intent_model key → the mis-phrased command is not recovered.
    cfg = _cfg()
    app = _make_app(cfg, "computer navigate to github.com", history=history)
    app._do_dictation(_audio())

    assert opened == []                      # nothing fired
    app.injector.inject.assert_not_called()  # unknown-command path, no paste
    assert history.recent_actions() == []    # no action logged


def test_intent_model_live_recovers_misphrased_action(temp_db, monkeypatch):
    history, _ = temp_db
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda u, **k: opened.append(u) or True)
    monkeypatch.setattr("src.notify.notify", lambda *a, **k: None)

    # "navigate to X" is not a classify() pattern (that's "go to X"); the model
    # recovers it and it fires through the normal open_url dispatch.
    cfg = _cfg(action_intent_model=True)
    app = _make_app(cfg, "computer navigate to github.com", history=history)
    app._do_dictation(_audio())

    assert opened == ["https://github.com"]
    app.injector.inject.assert_not_called()
    rows = history.recent_actions()
    assert rows and rows[0]["handler"] == "open_url" and rows[0]["ok"] is True


def test_intent_model_shadow_observes_without_executing(temp_db, monkeypatch):
    history, _ = temp_db
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda u, **k: opened.append(u) or True)
    monkeypatch.setattr("src.notify.notify", lambda *a, **k: None)

    cfg = _cfg(action_intent_model="shadow")
    app = _make_app(cfg, "computer navigate to github.com", history=history)
    app._do_dictation(_audio())

    # Shadow mode never executes the guess: no browser, no paste, no action row
    # (it falls through to the unknown-command path).
    assert opened == []
    app.injector.inject.assert_not_called()
    assert history.recent_actions() == []


def test_intent_model_cannot_launch_unconfigured_app(temp_db, monkeypatch):
    history, _ = temp_db
    launched = []
    # If dispatch ever reached the launcher this would record it — it must not.
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: launched.append(a) or MagicMock())
    monkeypatch.setattr("src.notify.notify", lambda *a, **k: None)

    # Model ON, but no apps configured. "launch spotify" predicts open(spotify);
    # re-validation refuses the unconfigured app, so nothing launches.
    cfg = _cfg(action_intent_model=True, action_apps={})
    app = _make_app(cfg, "computer launch spotify", history=history)
    app._do_dictation(_audio())

    assert launched == []
    app.injector.inject.assert_not_called()   # unknown-command path
    assert history.recent_actions() == []
