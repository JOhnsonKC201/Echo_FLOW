"""Prompt-Engineering styles: Simple / Reflection / Chain-of-Thought.

'simple' is the faithful one-shot cleanup (unchanged behaviour). The two
scaffold styles rewrite the dictation into a reasoning prompt (Draft→Reflect→
Refine, or brainstorm→score→build). All run under style=="prompt", so the
hallucination guard is bypassed and expansion is allowed.
"""
from __future__ import annotations

from src.cleanup import (Cleaner, build_pe_prompt, normalize_pe_style,
                         PE_STYLES, SYSTEM_PROMPTS)


def _cleaner(style="simple"):
    return Cleaner({
        "enabled": True, "provider": "ollama",
        "prompt_engineering": {"enabled": True, "audience": "claude-code",
                               "provider": "ollama", "style": style},
    })


# --- normalize_pe_style ------------------------------------------------------

def test_normalize_pe_style():
    assert normalize_pe_style("simple") == "simple"
    assert normalize_pe_style("Reflection") == "reflection"
    assert normalize_pe_style("chain-of-thought") == "chain_of_thought"
    assert normalize_pe_style("chain of thought") == "chain_of_thought"
    assert normalize_pe_style("") == "simple"
    assert normalize_pe_style("bogus") == "simple"
    assert set(PE_STYLES) == {"simple", "reflection", "chain_of_thought"}


# --- build_pe_prompt ---------------------------------------------------------

def test_build_pe_prompt_picks_the_right_scaffold():
    simple = build_pe_prompt("claude-code", "groq", "simple")
    refl = build_pe_prompt("claude-code", "groq", "reflection")
    cot = build_pe_prompt("claude-code", "groq", "chain_of_thought")

    assert "REFLECTION technique" not in simple and "CHAIN-OF-THOUGHT" not in simple
    assert "REFLECTION technique" in refl and "Draft:" in refl and "Reflect:" in refl
    assert "CHAIN-OF-THOUGHT technique" in cot and "brainstorm" in cot.lower()
    # Audience preamble is still applied to every style.
    for p in (simple, refl, cot):
        assert "CLAUDE CODE" in p


def test_scaffold_styles_drop_the_concise_length_hint():
    """The simple style tells a small local model to stay <=300 tokens; the
    scaffolds need room to expand, so that cap must not be applied to them."""
    simple = build_pe_prompt("generic", "ollama", "simple")
    refl = build_pe_prompt("generic", "ollama", "reflection")
    assert "<=300 tokens" in simple
    assert "<=300 tokens" not in refl
    assert "Never repeat" in refl        # keeps only the anti-echo note


def test_unknown_style_builds_the_simple_prompt():
    p = build_pe_prompt("generic", "groq", "nonsense")
    assert p == build_pe_prompt("generic", "groq", "simple")


# --- clean() reads the configured style --------------------------------------

def test_clean_uses_the_configured_pe_style(monkeypatch):
    cleaner = _cleaner(style="reflection")
    seen = {}
    monkeypatch.setattr(cleaner, "_via_ollama",
                        lambda system, text, *a, **k: seen.update(system=system)
                        or "Draft: do it.\nReflect: check it.\nRefine: redo it.")
    cleaner.clean("plan a trip on a budget", style="prompt",
                  provider_override="ollama")
    assert "REFLECTION technique" in seen["system"]


def test_clean_chain_of_thought_style(monkeypatch):
    cleaner = _cleaner(style="chain_of_thought")
    seen = {}
    monkeypatch.setattr(cleaner, "_via_ollama",
                        lambda system, text, *a, **k: seen.update(system=system)
                        or "Plan it. First brainstorm, then score, then build.")
    cleaner.clean("plan a trip", style="prompt", provider_override="ollama")
    assert "CHAIN-OF-THOUGHT technique" in seen["system"]


def test_pe_style_expansion_is_not_rejected_by_the_guard(monkeypatch):
    """A scaffold legitimately expands a short dictation several-fold. Because it
    runs under style=='prompt', the hallucination guard must not drop it."""
    cleaner = _cleaner(style="chain_of_thought")
    long_scaffold = ("Plan a weekend trip to Austin on a budget. First, "
                     "brainstorm the options. Then propose criteria to compare "
                     "them and pick the best. Next, evaluate each option. "
                     "Finally, build the plan from the best-scoring ones.")
    monkeypatch.setattr(cleaner, "_via_ollama", lambda *a, **k: long_scaffold)
    out, skipped = cleaner.clean("plan a trip", style="prompt",
                                 provider_override="ollama")
    assert not skipped
    assert "brainstorm" in out.lower()          # the expansion survived


def test_default_style_is_simple_and_faithful(monkeypatch):
    cleaner = _cleaner()          # no style set beyond default
    seen = {}
    monkeypatch.setattr(cleaner, "_via_ollama",
                        lambda system, text, *a, **k: seen.update(system=system)
                        or "Add a calculator to this project.")
    cleaner.clean("i want to make a calculator", style="prompt",
                  provider_override="ollama")
    assert "REFLECTION" not in seen["system"] and "CHAIN-OF-THOUGHT" not in seen["system"]
    assert "SENIOR ENGINEER'S VOICE ASSISTANT" in seen["system"]
