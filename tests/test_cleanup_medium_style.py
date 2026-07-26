"""The 'medium' cleanup style: grammar-fixing middle tier between default
(proofread only) and polished (free restructuring).

Covers: prompt registration + routing, the skip-fast-path bypass (grammar
slips pass the punctuation-only heuristic, so medium must always reach the
LLM), and registration in the style-profile and humanize allowlists.
"""
from __future__ import annotations

from src.cleanup import SYSTEM_PROMPTS, Cleaner


# Clean-looking by _is_already_clean's rules (capitalized, punctuated, short,
# no fillers) but grammatically wrong — exactly what medium exists to fix.
_CLEAN_LOOKING_BAD_GRAMMAR = "He dont know where the meeting is."


def _cleaner() -> Cleaner:
    return Cleaner({"enabled": True, "provider": "ollama", "skip_when_clean": True})


def test_medium_prompt_registered_and_routed(monkeypatch):
    assert "medium" in SYSTEM_PROMPTS
    cleaner = _cleaner()
    seen = {}

    def _fake_ollama(prompt, text, max_tokens=None, style="default"):
        seen["prompt"] = prompt
        return "He doesn't know where the meeting is."
    monkeypatch.setattr(cleaner, "_via_ollama", _fake_ollama)

    out, skipped = cleaner.clean(_CLEAN_LOOKING_BAD_GRAMMAR, style="medium")
    assert skipped is False
    assert seen["prompt"] == SYSTEM_PROMPTS["medium"]
    assert "doesn't" in out


def test_medium_bypasses_already_clean_skip(monkeypatch):
    """default skips the LLM on clean-looking input; medium must not."""
    cleaner = _cleaner()
    calls = []
    monkeypatch.setattr(
        cleaner, "_via_ollama",
        lambda prompt, text, max_tokens=None, style="default": calls.append(style) or text,
    )

    _, skipped_default = cleaner.clean(_CLEAN_LOOKING_BAD_GRAMMAR, style="default")
    assert skipped_default is True
    assert calls == []

    _, skipped_medium = cleaner.clean(_CLEAN_LOOKING_BAD_GRAMMAR, style="medium")
    assert skipped_medium is False
    assert calls == ["medium"]


def test_medium_prompt_forbids_restructuring():
    """The load-bearing constraints: grammar yes, compression/rewrite no."""
    p = SYSTEM_PROMPTS["medium"]
    assert "grammar" in p.lower()
    assert "compress" in p.lower()
    assert "hedge" in p.lower()


def test_medium_is_a_valid_style_profile(tmp_path):
    import sqlite3
    from src.dashboard import style_profiles as sp
    conn = sqlite3.connect(str(tmp_path / "h.db"))
    sp.replace_all(conn, [{"style": "medium", "matchers": []}])
    assert sp.pick_style(conn, "Any Window") == "medium"


def test_medium_is_humanize_eligible():
    from src.voice_profile import HUMANIZE_STYLES
    assert "medium" in HUMANIZE_STYLES
