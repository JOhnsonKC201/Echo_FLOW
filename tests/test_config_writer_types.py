"""Regression: config_writer must not silently coerce string settings to
int/bool/None via YAML type literals, and set_scalar must verify the
round-tripped VALUE, not just that the key still exists."""
from __future__ import annotations

import yaml
import pytest


def test_render_scalar_quotes_yaml_type_literals():
    from src.dashboard.config_writer import _render_scalar
    for literal in ["no", "yes", "on", "off", "true", "false", "null",
                    "123", "1.5", "~", "Y", "N"]:
        rendered = _render_scalar(literal)
        # Must round-trip back to the SAME string, not a bool/int/None.
        assert yaml.safe_load(f"k: {rendered}") == {"k": literal}, literal


def test_render_scalar_leaves_plain_strings_unquoted():
    from src.dashboard.config_writer import _render_scalar
    assert _render_scalar("hello") == "hello"
    assert _render_scalar("en") == "en"


def test_render_scalar_escapes_newline():
    from src.dashboard.config_writer import _render_scalar
    rendered = _render_scalar("hello\nworld")
    assert yaml.safe_load(f"k: {rendered}") == {"k": "hello\nworld"}


def test_set_scalar_preserves_string_type_end_to_end(tmp_path):
    from src.dashboard import config_writer as cw
    p = tmp_path / "config.yaml"
    p.write_text("whisper:\n  language: en\n", encoding="utf-8")
    cw.set_scalar(p, "whisper.language", "no")  # Norwegian ISO code
    loaded = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert loaded["whisper"]["language"] == "no"
    assert isinstance(loaded["whisper"]["language"], str)


def test_set_scalar_still_writes_real_ints_bare(tmp_path):
    from src.dashboard import config_writer as cw
    p = tmp_path / "config.yaml"
    p.write_text("audio:\n  silence_timeout_ms: 1500\n", encoding="utf-8")
    cw.set_scalar(p, "audio.silence_timeout_ms", 2000)
    loaded = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert loaded["audio"]["silence_timeout_ms"] == 2000
    assert isinstance(loaded["audio"]["silence_timeout_ms"], int)
