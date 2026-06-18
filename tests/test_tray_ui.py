"""Coverage for the non-blocking parts of TrayApp: icon rendering, dynamic
menu labels, the _safe action wrapper, and state updates.

The pystray run-loop and the real Windows tray icon are never exercised (no
display in CI) — but the pure logic around them is, which is where status/label
formatting bugs would actually live. Skips cleanly on the dep-light CI lane that
has neither Pillow nor pystray installed.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")        # icon rendering needs real Pillow
pytest.importorskip("pystray")    # menu construction needs real pystray

from src.tray import TrayApp, _make_icon


def _tray(**overrides):
    kw = dict(
        get_status=lambda: {"phase": "independent", "dictations": 12, "avg_quality": 87.4},
        on_pause_toggle=lambda: None,
        on_edit_last=lambda: None,
        on_open_history=lambda: None,
        on_open_graph=lambda: None,
        on_quit=lambda: None,
    )
    kw.update(overrides)
    return TrayApp(**kw)


def _raise(_=None):
    raise RuntimeError("boom")


# --- icon rendering -----------------------------------------------------------

@pytest.mark.parametrize("color", ["ok", "paused", "rec", "thinking", "bogus"])
def test_make_icon_returns_64px_rgba(color):
    """Every state (and an unknown color, which falls back to 'ok') renders a
    valid 64x64 RGBA glyph rather than raising."""
    img = _make_icon(active=True, color=color)
    assert img.size == (64, 64)
    assert img.mode == "RGBA"


def test_make_icon_inactive_takes_hollow_branch():
    # active=False uses fill=None on the mic body; must still be a valid image.
    img = _make_icon(active=False, color="paused")
    assert img.size == (64, 64)


# --- status / label formatting ------------------------------------------------

def test_status_text_formats_phase_count_and_quality():
    assert _tray()._status_text() == "Status: independent • 12 dictations • Q:87"


def test_status_text_omits_quality_when_absent():
    t = _tray(get_status=lambda: {"phase": "bootstrap", "dictations": 0})
    assert t._status_text() == "Status: bootstrap • 0 dictations"


def test_status_text_survives_status_error():
    assert _tray(get_status=_raise)._status_text() == "Status: unknown"


def test_pause_label_reflects_state():
    assert _tray(get_status=lambda: {"paused": True})._pause_label() == "▶  Resume"
    assert _tray(get_status=lambda: {"paused": False})._pause_label() == "⏸  Pause"


def test_pause_label_falls_back_on_error():
    assert _tray(get_status=_raise)._pause_label() == "Pause / Resume"


# --- _safe action wrapper -----------------------------------------------------

def test_safe_runs_action_then_refreshes():
    calls = []
    t = _tray()
    t.refresh = lambda: calls.append("refresh")
    t._safe(lambda: calls.append("action"))
    assert calls == ["action", "refresh"]


def test_safe_swallows_action_error_and_skips_refresh():
    """A menu action that raises must not crash the (windowless) tray thread;
    refresh() only runs on success."""
    t = _tray()
    refreshed = []
    t.refresh = lambda: refreshed.append(1)
    t._safe(_raise)  # must not raise
    assert refreshed == []


# --- state updates ------------------------------------------------------------

def test_set_state_without_icon_records_state_only():
    t = _tray()
    t._icon = None
    t.set_state("rec")
    assert t._state == "rec"


def test_set_state_updates_icon_when_present():
    class _Icon:
        icon = None
    t = _tray()
    t._icon = _Icon()
    t.set_state("rec")
    assert t._state == "rec"
    assert t._icon.icon is not None  # a fresh _make_icon was assigned


def test_build_menu_constructs_without_error():
    # _build_menu references pystray.Menu.SEPARATOR, which the conftest's
    # minimal stub doesn't provide. On the dep-light lane (pystray stubbed)
    # skip; the full lane has real pystray and exercises this.
    import pystray
    if getattr(pystray, "__stubbed_for_tests__", False):
        pytest.skip("needs real pystray (Menu.SEPARATOR), stubbed on the dep-light lane")
    t = _tray(on_open_dashboard=lambda: None, dashboard_hotkey_label="Ctrl+Shift+Alt")
    assert t._build_menu() is not None
