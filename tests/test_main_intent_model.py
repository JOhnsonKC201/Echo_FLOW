"""Phase 14+ — intent model wired into main._do_dictation.

Proves the safety-critical seam without a real App:
  - flag OFF (default) → a mis-phrased command is NOT recovered (byte-identical
    to today: it becomes an "unknown command", nothing fires)
  - flag ON → a mis-phrased command ("navigate to github.com") is recovered and
    fires through the SAME dispatch/log path as a regex hit
  - 'shadow' → the model's guess is observed but NEVER executed; it is
    PERSISTED to voice_actions (MODEL-SHADOW) so agreement can be measured
  - even ON, an unconfigured app can't be launched (allowlist stays authority)
"""
from __future__ import annotations

import json
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
    # MODEL-SHADOW provenance: the row records that the MODEL (not the regex)
    # produced this execution, so live-mode success can be measured per source.
    rec = json.loads(rows[0]["model_pred"])
    assert rec["recovered"] is True
    assert rec["action"] == "open_url"
    assert rec["backend"] == "keyword"


def test_intent_model_shadow_observes_without_executing(temp_db, monkeypatch):
    history, _ = temp_db
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda u, **k: opened.append(u) or True)
    monkeypatch.setattr("src.notify.notify", lambda *a, **k: None)

    cfg = _cfg(action_intent_model="shadow")
    app = _make_app(cfg, "computer navigate to github.com", history=history)
    app._do_dictation(_audio())

    # Shadow mode never executes the guess: no browser, no paste. The guess IS
    # persisted (MODEL-SHADOW) under the intent_shadow sentinel so recall on
    # real regex misses can be measured from the dashboard.
    assert opened == []
    app.injector.inject.assert_not_called()
    rows = history.recent_actions()
    assert len(rows) == 1
    row = rows[0]
    assert row["handler"] == "intent_shadow"
    rec = json.loads(row["model_pred"])
    assert rec["resolved"] is True and rec["action"] == "open_url"
    assert "recovered" not in rec            # nothing executed
    # Privacy: the spoken body is redacted at rest unless verbose logging is on.
    assert row["body"].startswith("<redacted")


def test_shadow_persists_agreement_on_regex_hit(temp_db, monkeypatch):
    history, _ = temp_db
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda u, **k: opened.append(u) or True)
    monkeypatch.setattr("src.notify.notify", lambda *a, **k: None)

    # "go to github.com" is a classify() regex HIT. In shadow mode the model
    # runs alongside (after dispatch) and its agreement is stored on the SAME
    # executed row — the online precision measurement on real utterances.
    cfg = _cfg(action_intent_model="shadow")
    app = _make_app(cfg, "computer go to github.com", history=history)
    app._do_dictation(_audio())

    assert opened == ["https://github.com"]          # regex path still fires
    rows = history.recent_actions()
    assert len(rows) == 1 and rows[0]["handler"] == "open_url"
    rec = json.loads(rows[0]["model_pred"])
    assert rec["agree"] is True
    assert rec["args_match"] is True
    assert rec["action"] == "open_url"


def test_shadow_compare_covers_prefix_free_hits(temp_db, monkeypatch):
    history, _ = temp_db
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda u, **k: opened.append(u) or True)
    monkeypatch.setattr("src.notify.notify", lambda *a, **k: None)

    # With action_require_prefix off, a bare "go to github.com" resolves and
    # fires via the prefix-free path. Shadow measurement deliberately covers
    # these executed hits too — same utterance semantics, more ground truth.
    cfg = _cfg(action_intent_model="shadow", action_require_prefix=False)
    app = _make_app(cfg, "go to github.com", history=history)
    app._do_dictation(_audio())

    assert opened == ["https://github.com"]
    rows = history.recent_actions()
    assert rows and rows[0]["handler"] == "open_url"
    rec = json.loads(rows[0]["model_pred"])
    assert rec["agree"] is True


def test_live_mode_regex_hit_skips_model_compare(temp_db, monkeypatch):
    history, _ = temp_db
    monkeypatch.setattr("webbrowser.open", lambda u, **k: True)
    monkeypatch.setattr("src.notify.notify", lambda *a, **k: None)

    # In live mode ('on') a regex hit never consults the model — no added
    # inference on the hot path, so the row carries no model_pred.
    cfg = _cfg(action_intent_model=True)
    app = _make_app(cfg, "computer go to github.com", history=history)
    app._do_dictation(_audio())

    rows = history.recent_actions()
    assert rows and rows[0]["model_pred"] is None


def test_shadow_persists_unresolved_guess(temp_db, monkeypatch):
    history, _ = temp_db
    launched = []
    monkeypatch.setattr("subprocess.Popen",
                        lambda *a, **k: launched.append(a) or MagicMock())
    monkeypatch.setattr("src.notify.notify", lambda *a, **k: None)

    # "launch spotify" gates, but no app is configured → the allowlist refuses
    # it. The shadow row still records the refusal (resolved: false): that is
    # the "model wanted an unconfigured app" signal, and nothing may launch.
    cfg = _cfg(action_intent_model="shadow", action_apps={})
    app = _make_app(cfg, "computer launch spotify", history=history)
    app._do_dictation(_audio())

    assert launched == []
    rows = history.recent_actions()
    assert len(rows) == 1 and rows[0]["handler"] == "intent_shadow"
    rec = json.loads(rows[0]["model_pred"])
    assert rec["resolved"] is False
    assert "action" not in rec
    assert rows[0]["label"] is None          # nothing resolved → nothing to name


def test_shadow_row_body_verbose_opt_in(temp_db, monkeypatch):
    history, _ = temp_db
    monkeypatch.setattr("webbrowser.open", lambda u, **k: True)
    monkeypatch.setattr("src.notify.notify", lambda *a, **k: None)

    cfg = _cfg(action_intent_model="shadow", action_log_verbose=True)
    app = _make_app(cfg, "computer navigate to github.com", history=history)
    app._do_dictation(_audio())

    row = history.recent_actions()[0]
    assert row["handler"] == "intent_shadow"
    assert row["body"] == "navigate to github.com"   # verbose keeps the utterance


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
