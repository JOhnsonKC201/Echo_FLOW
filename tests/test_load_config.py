"""Regression: load_config must fail with an actionable error (not a cryptic
AttributeError deep in App.__init__) when config.yaml is empty/whitespace —
yaml.safe_load returns None for such a file."""
from __future__ import annotations

import pytest

import src.main as main


def test_empty_config_raises_actionable_error(tmp_path, monkeypatch):
    p = tmp_path / "config.yaml"
    p.write_text("   \n\n", encoding="utf-8")
    monkeypatch.setattr(main, "CONFIG_PATH", p)
    with pytest.raises(ValueError, match="empty or invalid"):
        main.load_config()


def test_non_mapping_config_raises(tmp_path, monkeypatch):
    p = tmp_path / "config.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    monkeypatch.setattr(main, "CONFIG_PATH", p)
    with pytest.raises(ValueError, match="mapping"):
        main.load_config()


def test_valid_config_loads(tmp_path, monkeypatch):
    p = tmp_path / "config.yaml"
    p.write_text("history:\n  db_path: x.db\n", encoding="utf-8")
    monkeypatch.setattr(main, "CONFIG_PATH", p)
    cfg = main.load_config()
    assert cfg["history"]["db_path"] == "x.db"
