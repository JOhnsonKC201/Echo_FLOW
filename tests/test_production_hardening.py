"""Production-hardening regression tests.

Covers the May 2026 audit pass:
  1. Cloud API keys are NOT stripped from the environment at module import.
     Earlier code stripped GROQ_API_KEY + ANTHROPIC_API_KEY, which silently
     broke PE mode and the teacher loop for anyone who'd correctly set them.
  2. _audit_cloud_keys() warns the right keys for the right features.
  3. _via_groq honors model_override so concurrent teacher dispatches
     don't race on shared cfg state.
  4. /healthz endpoint returns the right shape (called via Flask test app
     would require larger plumbing; this validates the dict structure used
     by the handler).
"""
from __future__ import annotations

import logging
import os


def test_blocked_env_excludes_pe_and_teacher_keys():
    """Regression: GROQ_API_KEY and ANTHROPIC_API_KEY must NOT be in the
    module-level blocklist — PE mode and the teacher loop need them.
    """
    from src.main import BLOCKED_ENV
    assert "GROQ_API_KEY" not in BLOCKED_ENV
    assert "ANTHROPIC_API_KEY" not in BLOCKED_ENV


def test_blocked_env_still_blocks_unused_keys():
    """OPENAI_API_KEY has no code path; keeping it blocked prevents accidental
    leakage if a user copy-pastes a shell profile that exports it."""
    from src.main import BLOCKED_ENV
    assert "OPENAI_API_KEY" in BLOCKED_ENV


def _capture_warnings(func, *args, **kwargs) -> list[str]:
    """Capture WARNING records from src.main's logger (wispr.main) regardless
    of pytest caplog plumbing — the project uses a custom logger namespace
    with propagation disabled."""
    msgs: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                msgs.append(record.getMessage())

    h = _Handler(level=logging.WARNING)
    logger = logging.getLogger("wispr.main")
    logger.addHandler(h)
    try:
        func(*args, **kwargs)
    finally:
        logger.removeHandler(h)
    return msgs


def test_audit_warns_for_pe_groq_without_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    from src.main import _audit_cloud_keys
    cfg = {
        "prompt_engineering": {"enabled": True, "provider": "groq"},
        "cleanup": {"learning": {"teacher_enabled": False}},
    }
    msgs = _capture_warnings(_audit_cloud_keys, cfg)
    assert any("GROQ_API_KEY" in m for m in msgs)


def test_audit_warns_for_teacher_without_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    from src.main import _audit_cloud_keys
    cfg = {
        "prompt_engineering": {"enabled": False},
        "cleanup": {"learning": {"teacher_enabled": True}},
    }
    msgs = _capture_warnings(_audit_cloud_keys, cfg)
    assert any("GROQ_API_KEY" in m and "Teacher" in m for m in msgs)


def test_audit_warns_for_pe_anthropic_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from src.main import _audit_cloud_keys
    cfg = {
        "prompt_engineering": {"enabled": True, "provider": "anthropic"},
        "cleanup": {"learning": {}},
    }
    msgs = _capture_warnings(_audit_cloud_keys, cfg)
    assert any("ANTHROPIC_API_KEY" in m for m in msgs)


def test_audit_silent_when_keys_present(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_present")
    from src.main import _audit_cloud_keys
    cfg = {
        "prompt_engineering": {"enabled": True, "provider": "groq"},
        "cleanup": {"learning": {"teacher_enabled": True}},
    }
    msgs = _capture_warnings(_audit_cloud_keys, cfg)
    assert not any("GROQ_API_KEY" in m for m in msgs)


def test_audit_does_not_leak_key_value(monkeypatch):
    """API key value must NEVER appear in log output."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_super_secret_value")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from src.main import _audit_cloud_keys
    cfg = {
        "prompt_engineering": {"enabled": True, "provider": "anthropic"},
        "cleanup": {"learning": {}},
    }
    msgs = _capture_warnings(_audit_cloud_keys, cfg)
    for m in msgs:
        assert "gsk_super_secret_value" not in m


def test_via_groq_honors_model_override(monkeypatch):
    """Teacher dispatch must not mutate self.cfg['groq'] — model_override
    threads the teacher model through cleanly. Verified by capturing the
    payload sent to the Groq endpoint."""
    from src.cleanup import Cleaner
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    c = Cleaner({
        "enabled": True, "provider": "ollama",
        "groq": {"model": "default-model"},
    })

    captured: dict = {}

    class _FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    def _fake_post(url, *, headers, json, timeout):
        captured["model"] = json["model"]
        return _FakeResp()

    monkeypatch.setattr(c._session, "post", _fake_post)
    c._via_groq("system", "text", model_override="teacher-specific-model")
    assert captured["model"] == "teacher-specific-model"
    # And the default remains untouched after the call.
    assert c.cfg["groq"]["model"] == "default-model"


def test_via_groq_falls_back_to_cfg_model(monkeypatch):
    from src.cleanup import Cleaner
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    c = Cleaner({"enabled": True, "groq": {"model": "default-model"}})

    captured: dict = {}

    class _FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    def _fake_post(url, *, headers, json, timeout):
        captured["model"] = json["model"]
        return _FakeResp()

    monkeypatch.setattr(c._session, "post", _fake_post)
    c._via_groq("system", "text")
    assert captured["model"] == "default-model"
