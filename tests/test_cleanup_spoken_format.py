"""Spoken formatting is applied by Cleaner.clean() on every path.

The pass has to run before the model, before the already-clean fast path, and
even when cleanup is disabled, because it is the only way a user with no model
gets a line break out of "new line".
"""
from __future__ import annotations

from src.cleanup import SYSTEM_PROMPTS, Cleaner


def test_runs_when_cleanup_is_disabled():
    cleaner = Cleaner({"enabled": False})
    out, skipped = cleaner.clean("first line new line second line")
    assert out == "first line\nsecond line"
    assert skipped is False


def test_runs_before_the_already_clean_fast_path():
    # Short, capitalized, terminally punctuated: the fast path would skip the
    # model. The command must already be converted by then.
    cleaner = Cleaner({"enabled": True, "provider": "ollama", "skip_when_clean": True})
    out, skipped = cleaner.clean("Ship it. Full stop.")
    assert out == "Ship it."
    assert skipped is True


def test_model_sees_the_converted_text(monkeypatch):
    cleaner = Cleaner({"enabled": True, "provider": "ollama", "skip_when_clean": False})
    seen = {}

    def _fake(system, text, **kw):
        seen["text"] = text
        return text

    monkeypatch.setattr(cleaner, "_via_ollama", _fake)
    cleaner.clean("um hello comma world new paragraph bye")
    assert seen["text"] == "um hello, world\n\nbye"


def test_can_be_switched_off():
    cleaner = Cleaner({"enabled": False, "spoken_formatting": False})
    out, _ = cleaner.clean("first line new line second line")
    assert out == "first line new line second line"


def test_prompt_mode_is_left_alone():
    # Prompt-engineering output is a prompt for another model; the words are
    # the user's instructions and must not be rewritten.
    cleaner = Cleaner({"enabled": False})
    out, _ = cleaner.clean("say new line here", style="prompt")
    assert out == "say new line here"


def test_prose_prompts_carry_the_self_correction_rule():
    for style in ("default", "medium", "polished", "casual", "email"):
        assert "SELF-CORRECTIONS" in SYSTEM_PROMPTS[style], style
        assert "LINE BREAKS" in SYSTEM_PROMPTS[style], style
    assert "SELF-CORRECTIONS" not in SYSTEM_PROMPTS["code"]
