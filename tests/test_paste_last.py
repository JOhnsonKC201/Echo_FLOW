"""Smoke tests for the Ctrl+Win re-paste handler in src/main.py."""
from __future__ import annotations

import sqlite3
import types

import pytest


def _make_app_like(db_path: str, latest_text: str | None = "Hello world."):
    """Build a minimal stand-in for App with just what _on_paste_last touches."""
    from src.main import App
    app = App.__new__(App)   # bypass __init__ — we wire only what we need

    # History stand-in: just the conn attribute + a dictations table.
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dictations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL DEFAULT 0,
            cleaned_text TEXT
        )
    """)
    if latest_text is not None:
        conn.execute("INSERT INTO dictations(ts, cleaned_text) VALUES (1, 'older')")
        conn.execute("INSERT INTO dictations(ts, cleaned_text) VALUES (2, ?)", (latest_text,))
    conn.commit()

    history = types.SimpleNamespace(conn=conn)
    app.history = history
    app._paused = False
    app._active = False
    app._last_cleaned_text = None
    app.cfg = {"sound": {"enabled": False}}
    app.tray = None

    # Capture injector calls instead of pasting for real.
    captured = []
    app.injector = types.SimpleNamespace(inject=lambda txt: captured.append(txt))
    return app, captured


def test_paste_last_returns_latest_dictation(tmp_path):
    db = str(tmp_path / "history.db")
    app, captured = _make_app_like(db, latest_text="Hello world.")
    app._on_paste_last()
    assert captured == ["Hello world."]


def test_paste_last_silent_when_history_empty(tmp_path):
    db = str(tmp_path / "history.db")
    app, captured = _make_app_like(db, latest_text=None)
    # Should not raise, should not inject. Notify is safe to call but we
    # tolerate either outcome.
    app._on_paste_last()
    assert captured == []


def test_paste_last_skipped_when_recording_active(tmp_path):
    db = str(tmp_path / "history.db")
    app, captured = _make_app_like(db, latest_text="Hello world.")
    app._active = True
    app._on_paste_last()
    assert captured == []


def test_paste_last_skipped_when_paused(tmp_path):
    db = str(tmp_path / "history.db")
    app, captured = _make_app_like(db, latest_text="Hello world.")
    app._paused = True
    app._on_paste_last()
    assert captured == []


def test_paste_last_prefers_in_memory_over_db(tmp_path):
    """If _last_cleaned_text is set, it should win over the DB query
    — this is the race-fix for the async DB write."""
    db = str(tmp_path / "history.db")
    # DB says "old text" but in-memory says "fresh text" → should paste fresh.
    app, captured = _make_app_like(db, latest_text="old text")
    app._last_cleaned_text = "fresh text just spoken"
    app._on_paste_last()
    assert captured == ["fresh text just spoken"]


def test_combo_parses(tmp_path):
    """Sanity: 'ctrl+win' must parse to the (Ctrl, Cmd) key set."""
    from src.hotkey import _parse_combo
    from pynput import keyboard
    parsed = _parse_combo("ctrl+win")
    assert keyboard.Key.ctrl in parsed
    assert keyboard.Key.cmd in parsed
    assert len(parsed) == 2
