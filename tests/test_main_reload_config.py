"""App.reload_config — hot-reload of dictionary / cleanup / PE settings.

reload_config does NOT re-read config.yaml from disk: the dashboard mutates
``app.cfg`` in place and then calls reload_config() to re-derive the hot
state from it. So these tests simulate a "config change" by swapping the
dict bound to ``app.cfg`` between calls.

It must refresh, without a daemon restart:
  - transcriber.cfg.initial_prompt (Whisper decoder bias) from the merged
    custom vocabulary (static cfg list + dashboard dictionary table +
    snippet expansions + learner-mined terms)
  - self._pe_cfg (prompt-engineering block)
  - learner trust flags (trust_mobile / trust_teacher) + cache invalidation
  - cleaner.cfg rebind + casing-cache invalidation

And a config object that raises mid-reload must NOT crash the daemon and
must leave the previously derived state untouched (the whole body is
guarded by a try/except that only logs a warning).
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock

from src.main import App


def _make_app(cfg, history=None, with_learner=True):
    """Bare App with only the attributes reload_config / _build_custom_vocabulary read."""
    app = App.__new__(App)
    app.cfg = cfg
    app.history = history
    app._pe_cfg = {"enabled": False}

    # transcriber only needs a .cfg with an initial_prompt slot
    app.transcriber = types.SimpleNamespace(
        cfg=types.SimpleNamespace(initial_prompt="OLD PROMPT")
    )

    if with_learner:
        app.learner = MagicMock()
        app.learner.cfg = types.SimpleNamespace(trust_mobile=False, trust_teacher=True)
        app.learner.personal_vocabulary.return_value = []
    else:
        app.learner = None

    app.cleaner = MagicMock()
    app.cleaner.cfg = {"marker": "old-cleaner-cfg"}
    return app


def _cfg(**overrides):
    base = {
        "custom_vocabulary": ["Kubernetes", "EchoFlow"],
        "cleanup": {
            "snippets": {"sig": "Best regards Johnson"},
            "learning": {"trust_mobile": True, "trust_teacher": False},
        },
        "prompt_engineering": {"enabled": True, "provider": "groq"},
    }
    base.update(overrides)
    return base


def test_reload_refreshes_prompt_pe_learner_and_cleaner():
    cfg = _cfg()
    app = _make_app(cfg)

    app.reload_config()

    # Whisper bias rebuilt from static vocab + snippet expansions.
    ip = app.transcriber.cfg.initial_prompt
    assert isinstance(ip, str) and ip != "OLD PROMPT"
    assert "Kubernetes" in ip
    assert "EchoFlow" in ip
    assert "Best regards Johnson" in ip  # snippet expansion lands in the prior

    # Prompt-engineering block re-read from cfg.
    assert app._pe_cfg == {"enabled": True, "provider": "groq"}

    # Learner trust flags refreshed from cleanup.learning + cache dropped.
    assert app.learner.cfg.trust_mobile is True
    assert app.learner.cfg.trust_teacher is False
    app.learner.invalidate_cache.assert_called_once()

    # Cleaner rebound to the new cleanup block + casing cache dropped.
    assert app.cleaner.cfg is cfg["cleanup"]
    app.cleaner.invalidate_casing_cache.assert_called_once()


def test_reload_picks_up_changed_config_values():
    """Simulates the dashboard mutating config then calling reload_config:
    the second reload must reflect the NEW values, not the first ones."""
    app = _make_app(_cfg())
    app.reload_config()
    assert "Kubernetes" in app.transcriber.cfg.initial_prompt

    # "Change the config on disk" → rebind app.cfg to the new values.
    app.cfg = _cfg(
        custom_vocabulary=["Zeroconf"],
        prompt_engineering={"enabled": False},
    )
    app.cfg["cleanup"]["learning"] = {"trust_mobile": False, "trust_teacher": True}

    app.reload_config()

    ip = app.transcriber.cfg.initial_prompt
    assert "Zeroconf" in ip
    assert "Kubernetes" not in ip                  # stale term really gone
    assert app._pe_cfg == {"enabled": False}       # PE block refreshed
    assert app.learner.cfg.trust_mobile is False   # flags follow the new cfg
    assert app.learner.cfg.trust_teacher is True
    assert app.learner.invalidate_cache.call_count == 2


def test_reload_merges_dashboard_dictionary_terms(temp_db):
    """The stated purpose of reload_config: dictionary additions made through
    the dashboard (custom_vocabulary table) take effect on the next dictation."""
    history, _path = temp_db
    from src.dashboard import vocabulary as vocab_mod
    vocab_mod.add_term(history.conn, "Anthropic")
    vocab_mod.add_term(history.conn, "pyannote")

    app = _make_app(_cfg(custom_vocabulary=[]), history=history)
    app.reload_config()

    ip = app.transcriber.cfg.initial_prompt
    assert "Anthropic" in ip
    assert "pyannote" in ip


def test_reload_with_no_vocab_clears_initial_prompt():
    """No vocabulary from any source → the bias prompt is reset to None,
    not left at its previous value."""
    cfg = {
        "custom_vocabulary": [],
        "cleanup": {},
        "prompt_engineering": {"enabled": False},
    }
    app = _make_app(cfg, with_learner=False)
    app.reload_config()
    assert app.transcriber.cfg.initial_prompt is None


class _CorruptCfg:
    """Stands in for a config whose load/parse blew up: every access raises."""
    def get(self, *a, **k):
        raise RuntimeError("corrupt config")

    def __getitem__(self, k):
        raise RuntimeError("corrupt config")


def test_corrupt_config_does_not_crash_and_keeps_previous_state():
    app = _make_app(_cfg())
    app.reload_config()  # derive good state first
    good_prompt = app.transcriber.cfg.initial_prompt
    good_pe = app._pe_cfg
    good_cleaner_cfg = app.cleaner.cfg
    assert "Kubernetes" in good_prompt

    app.cfg = _CorruptCfg()
    app.reload_config()  # must not raise — outer try/except logs a warning

    # Everything previously derived is still in place.
    assert app.transcriber.cfg.initial_prompt == good_prompt
    assert app._pe_cfg is good_pe
    assert app.cleaner.cfg is good_cleaner_cfg
    assert app.learner.cfg.trust_mobile is True   # untouched from the good reload
    # Only the one successful reload invalidated caches.
    assert app.learner.invalidate_cache.call_count == 1
    assert app.cleaner.invalidate_casing_cache.call_count == 1
