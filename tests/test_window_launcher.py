"""Tests for src.dashboard.window — state persistence + port resolution + healthcheck.

These cover the pure / mostly-pure helpers. The webview.start() path requires
a GUI and is exercised via integration smoke elsewhere.
"""
from __future__ import annotations

import json
import time
import types

import pytest


# --- state file ------------------------------------------------------------

def test_load_window_state_missing_file_returns_empty(tmp_path, monkeypatch):
    from src.dashboard import window as W
    monkeypatch.setattr(W, "_STATE_FILE", tmp_path / "does-not-exist.json")
    assert W._load_window_state() == {}


def test_load_window_state_reads_valid_dict(tmp_path, monkeypatch):
    from src.dashboard import window as W
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"width": 999, "height": 555}), encoding="utf-8")
    monkeypatch.setattr(W, "_STATE_FILE", p)
    assert W._load_window_state() == {"width": 999, "height": 555}


def test_load_window_state_corrupt_file_returns_empty(tmp_path, monkeypatch):
    from src.dashboard import window as W
    p = tmp_path / "state.json"
    p.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(W, "_STATE_FILE", p)
    assert W._load_window_state() == {}


def test_save_window_state_writes_width_height_schema(tmp_path, monkeypatch):
    from src.dashboard import window as W
    state_path = tmp_path / "sub" / "state.json"  # exercise the mkdir branch
    monkeypatch.setattr(W, "_STATE_FILE", state_path)

    # Fake pywebview window that exposes get_size() like the real one does.
    fake_win = types.SimpleNamespace(
        get_size=lambda: (1234, 678),
        width=0, height=0,
    )
    W._save_window_state(fake_win)

    assert state_path.exists()
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data == {"width": 1234, "height": 678}


def test_save_window_state_falls_back_to_static_attrs(tmp_path, monkeypatch):
    """When get_size() raises, _save_window_state should still write defaults."""
    from src.dashboard import window as W
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(W, "_STATE_FILE", state_path)

    def _boom():
        raise RuntimeError("no live size")
    fake_win = types.SimpleNamespace(get_size=_boom, width=1280, height=820)
    W._save_window_state(fake_win)

    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data == {"width": 1280, "height": 820}


def test_save_window_state_ignores_maximized_size(tmp_path, monkeypatch):
    """A close while maximized reports screen-sized dims; those must NOT become
    the persisted restore-down size (else it ratchets to full screen forever)."""
    from src.dashboard import window as W
    p = tmp_path / "state.json"
    monkeypatch.setattr(W, "_STATE_FILE", p)
    monkeypatch.setattr(W, "_primary_screen_size", lambda: (1920, 1080))
    fake_win = types.SimpleNamespace(get_size=lambda: (1920, 1080), width=0, height=0)
    W._save_window_state(fake_win)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data == {"width": 1280, "height": 820}  # default, not the maximized size


def test_save_window_state_swallows_all_errors(tmp_path, monkeypatch):
    """Best-effort: must never propagate exceptions (it runs on window-close)."""
    from src.dashboard import window as W
    # Point at a path whose parent we will make unwritable by mocking write_text.
    monkeypatch.setattr(W, "_STATE_FILE", tmp_path / "x.json")

    bad_win = types.SimpleNamespace(
        get_size=lambda: (_ for _ in ()).throw(OSError("nope")),
    )
    # Should not raise.
    W._save_window_state(bad_win)


# --- port resolution -------------------------------------------------------

def test_resolve_port_explicit_wins():
    from src.dashboard import window as W
    assert W._resolve_port(8888) == 8888


def test_resolve_port_reads_port_file_when_no_explicit(monkeypatch):
    from src.dashboard import window as W
    import src.dashboard as _d
    monkeypatch.setattr(_d, "read_port_file", lambda: 49999)
    assert W._resolve_port(None) == 49999


def test_resolve_port_falls_back_to_default(monkeypatch):
    from src.dashboard import window as W
    import src.dashboard as _d
    monkeypatch.setattr(_d, "read_port_file", lambda: None)
    assert W._resolve_port(None) == 8766


# --- healthcheck poll ------------------------------------------------------

def test_wait_for_server_returns_false_for_unreachable_url():
    from src.dashboard import window as W
    # Port 1 on loopback is unbindable / unreachable in user-space. Even if
    # something miraculously did answer, it won't be HTTP-200 on /api/healthz.
    started = time.monotonic()
    assert W._wait_for_server("http://127.0.0.1:1/api/healthz", timeout_s=0.6) is False
    # Should bail close to the timeout, not hang forever.
    elapsed = time.monotonic() - started
    assert elapsed < 3.0


def test_wait_for_server_returns_true_when_url_responds(monkeypatch):
    from src.dashboard import window as W
    import urllib.request

    class _FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: _FakeResp())
    assert W._wait_for_server("http://example.invalid/api/healthz",
                              timeout_s=2.0) is True


# --- primary screen size ---------------------------------------------------

def test_primary_screen_size_returns_two_ints():
    """Must not crash on any platform; falls back to (1920, 1080) off-Windows."""
    from src.dashboard import window as W
    w, h = W._primary_screen_size()
    assert isinstance(w, int) and isinstance(h, int)
    assert w > 0 and h > 0


# --- icon resolution -------------------------------------------------------

def test_icon_path_resolves_to_existing_file():
    """From source, _icon_path() returns a real bundled icon (or None if the
    assets are absent — never a path that doesn't exist)."""
    import os
    from src.dashboard import window as W
    p = W._icon_path()
    assert p is None or os.path.exists(p)
