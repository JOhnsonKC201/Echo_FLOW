"""Shared pytest fixtures. Tests must not require Groq/network."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Make `src` importable as a package from the project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def temp_db(tmp_path):
    """A clean SQLite DB with the dictations schema, in a temp dir."""
    from src.history import History
    db_path = tmp_path / "test_history.db"
    h = History(str(db_path))
    yield h, str(db_path)
    try:
        h.conn.close()
    except Exception:
        pass


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """Run tests with no real GROQ_API_KEY leaking in."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
