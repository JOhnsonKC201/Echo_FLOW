"""main._do_dictation — the "My Voice" humanize seam.

Exercises the wiring without a real App or LLM: the cleaner is mocked, and
voice_profile.build is patched to a fixed profile. Asserts the gate (off/on/
shadow/forced), that a failed humanize falls back to the cleaned text, and that
shadow never changes the pasted text.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from src.main import App
from src.history import History


CLEAN = "this is the cleaned dictation text that we start from"


def _make_app(cfg, history=None):
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
    app.transcriber.transcribe.return_value = (CLEAN, "en", {})
    app.cleaner = MagicMock()
    app.cleaner.pick_style.return_value = "default"        # in HUMANIZE_STYLES
    app.cleaner.clean.return_value = (CLEAN, False)
    app.cleaner.humanize.return_value = None
    app.cleaner._voice_similarity.return_value = 0.9
    app.injector = MagicMock()
    app.injector.trailing_space = True
    return app


def _audio():
    return np.ones(16000, dtype=np.float32) * 0.1


def _cfg(**experimental):
    return {"audio": {"sample_rate": 16000}, "experimental": experimental}


def _pasted(app):
    assert app.injector.inject.called, "nothing was pasted"
    return app.injector.inject.call_args.args[0]


@pytest.fixture(autouse=True)
def _profile(monkeypatch):
    """Default: a non-empty profile so the seam proceeds. Individual tests can
    override to '' to exercise the empty-profile skip."""
    monkeypatch.setattr("src.voice_profile.build", lambda *a, **k: "PROFILE")
    import src.voice_profile as vp
    vp.invalidate()


def test_off_is_noop(tmp_path):
    app = _make_app(_cfg(humanize=False), History(str(tmp_path / "h.db")))
    app._do_dictation(_audio())
    app.cleaner.humanize.assert_not_called()
    assert _pasted(app) == CLEAN


def test_on_replaces_when_guards_pass(tmp_path):
    app = _make_app(_cfg(humanize=True), History(str(tmp_path / "h.db")))
    app.cleaner.humanize.return_value = "the same idea, in my own voice"
    app._do_dictation(_audio())
    app.cleaner.humanize.assert_called_once()
    assert _pasted(app) == "the same idea, in my own voice"


def test_on_provider_none_keeps_cleaned(tmp_path):
    app = _make_app(_cfg(humanize=True), History(str(tmp_path / "h.db")))
    app.cleaner.humanize.return_value = None      # dead provider / guard reject
    app._do_dictation(_audio())
    assert _pasted(app) == CLEAN                   # dictation still pastes


def test_on_empty_profile_skips(tmp_path, monkeypatch):
    monkeypatch.setattr("src.voice_profile.build", lambda *a, **k: "")
    app = _make_app(_cfg(humanize=True), History(str(tmp_path / "h.db")))
    app._do_dictation(_audio())
    app.cleaner.humanize.assert_not_called()
    assert _pasted(app) == CLEAN


def test_polish_skipped_skips_on(tmp_path):
    app = _make_app(_cfg(humanize=True), History(str(tmp_path / "h.db")))
    app.cleaner.clean.return_value = (CLEAN, True)     # polish_skipped
    app._do_dictation(_audio())
    app.cleaner.humanize.assert_not_called()


def test_foreign_transform_override_skips(tmp_path):
    app = _make_app(_cfg(humanize=True), History(str(tmp_path / "h.db")))
    app._armed_transform = {"name": "Formal", "builtin": True,
                            "system_prompt": "Rewrite formally."}
    app._do_dictation(_audio())
    app.cleaner.humanize.assert_not_called()           # override owns the output


def test_forced_my_voice_routes_and_disarms(tmp_path):
    # humanize OFF globally, but the My Voice transform is armed → one-shot fires.
    app = _make_app(_cfg(humanize=False), History(str(tmp_path / "h.db")))
    app._armed_transform = {"name": "My Voice", "builtin": True,
                            "system_prompt": "(doc)"}
    app.cleaner.humanize.return_value = "forced voice rewrite"
    app._do_dictation(_audio())
    app.cleaner.humanize.assert_called_once()
    assert _pasted(app) == "forced voice rewrite"
    assert app._armed_humanize is False                # disarmed after one use
    assert app._armed_transform is None


def test_on_threads_min_sim_and_local_by_default(tmp_path):
    app = _make_app(_cfg(humanize=True, humanize_min_sim=0.7,
                         humanize_use_cloud=True), History(str(tmp_path / "h.db")))
    # cloud requested but allow_cloud_cleanup is absent → must stay local.
    app.cfg["cleanup"] = {"allow_cloud_cleanup": False}
    app.cleaner.humanize.return_value = "voiced"
    app._do_dictation(_audio())
    kw = app.cleaner.humanize.call_args.kwargs
    assert kw["min_sim"] == 0.7
    assert kw["use_cloud"] is False        # both flags required for cloud


def test_on_cloud_when_both_flags_set(tmp_path):
    app = _make_app(_cfg(humanize=True, humanize_use_cloud=True),
                    History(str(tmp_path / "h.db")))
    app.cfg["cleanup"] = {"allow_cloud_cleanup": True}
    app.cleaner.humanize.return_value = "voiced"
    app._do_dictation(_audio())
    assert app.cleaner.humanize.call_args.kwargs["use_cloud"] is True


def test_shadow_logs_without_changing_paste(tmp_path):
    h = History(str(tmp_path / "h.db"))
    app = _make_app(_cfg(humanize="shadow"), h)
    app.cleaner.humanize.return_value = "what my voice would have said"
    app._do_dictation(_audio())

    # Pasted text is UNCHANGED in shadow mode.
    assert _pasted(app) == CLEAN
    # The would-have-produced text is logged (background thread) — poll for it.
    deadline = time.time() + 3.0
    rows = []
    while time.time() < deadline:
        rows = h.recent_humanize_shadow(5)
        if rows:
            break
        time.sleep(0.05)
    assert rows, "shadow row was never logged"
    assert rows[0]["cleaned_text"] == CLEAN
    assert rows[0]["humanized_text"] == "what my voice would have said"
    assert rows[0]["similarity"] == 0.9
