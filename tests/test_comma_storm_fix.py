"""Regression tests for the Whisper comma-storm bug.

When the Whisper decoder is fed a comma-separated `initial_prompt`
("Vocabulary: foo, bar, baz"), it learns to emit comma-separated
transcripts ("Hello, world, today."). Two layers defend against this:

  1. `_format_initial_prompt` wraps vocab in a fluent sentence with
     space-separated terms (no commas).
  2. `_polish_text` detects the comma-storm signature post-cleanup and
     flattens commas back to spaces as a safety net.
"""
from __future__ import annotations


def test_format_initial_prompt_uses_no_commas():
    from src.main import _format_initial_prompt
    out = _format_initial_prompt(["Kubernetes", "Ollama", "Echo", "Whisper"])
    assert "," not in out
    assert "Kubernetes Ollama Echo Whisper" in out
    assert out.endswith(".")


def test_format_initial_prompt_dedupes_and_skips_empty():
    from src.main import _format_initial_prompt
    out = _format_initial_prompt(["foo", "", "foo", "bar", "  ", "baz"])
    # foo only once; empties dropped
    assert out.count("foo") == 1
    assert "bar" in out and "baz" in out
    assert "," not in out


def test_format_initial_prompt_handles_empty():
    from src.main import _format_initial_prompt
    assert _format_initial_prompt([]) == ""
    assert _format_initial_prompt(["", "  "]) == ""


def test_polish_text_flattens_comma_storm():
    from src.cleanup import _polish_text
    # The pathological pattern from the bug report.
    s = "What, can, be, Improving, This, System?"
    out = _polish_text(s)
    assert "," not in out
    # Sentence should be reconstructed with proper capitalization.
    assert out.startswith("What ")
    assert out.endswith("?")


def test_polish_text_flattens_long_comma_storm():
    from src.cleanup import _polish_text
    s = "Schedule, Start, Don't, Unhook, 45, Minutes."
    out = _polish_text(s)
    assert "," not in out
    assert out.startswith("Schedule ")
    assert out.endswith(".")


def test_polish_text_preserves_legitimate_commas():
    from src.cleanup import _polish_text
    # A real sentence with three commas, not a comma-storm — should be left alone.
    s = "I went to the store, bought some milk, and came home."
    out = _polish_text(s)
    assert out.count(",") == 2
    assert "store, bought some milk" in out


def test_polish_text_preserves_short_commas():
    from src.cleanup import _polish_text
    s = "Hello, world."
    out = _polish_text(s)
    # Two cells, well below the 4-comma trigger threshold.
    assert "Hello, world" in out
