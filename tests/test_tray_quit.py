"""TrayApp quit ordering: icon.stop() must run BEFORE on_quit().

on_quit is tray_quit() in the daemon, which ends in os._exit() — anything
sequenced after it never executes. With the old order (on_quit first), the
pystray cleanup was dead code and Windows could keep a ghost tray icon.
"""
from __future__ import annotations

from src.tray import TrayApp


def _make_tray(on_quit):
    return TrayApp(
        get_status=lambda: {},
        on_pause_toggle=lambda: None,
        on_edit_last=lambda: None,
        on_open_history=lambda: None,
        on_open_graph=lambda: None,
        on_quit=on_quit,
    )


class _FakeIcon:
    def __init__(self, calls, fail=False):
        self._calls = calls
        self._fail = fail

    def stop(self):
        self._calls.append("icon.stop")
        if self._fail:
            raise RuntimeError("boom")


def test_quit_stops_icon_before_on_quit():
    calls: list[str] = []
    tray = _make_tray(on_quit=lambda: calls.append("on_quit"))
    tray._icon = _FakeIcon(calls)
    tray._quit()
    assert calls == ["icon.stop", "on_quit"]


def test_quit_still_quits_if_icon_stop_fails():
    """A pystray cleanup failure must never block the actual quit."""
    calls: list[str] = []
    tray = _make_tray(on_quit=lambda: calls.append("on_quit"))
    tray._icon = _FakeIcon(calls, fail=True)
    tray._quit()
    assert calls == ["icon.stop", "on_quit"]


def test_quit_without_icon_still_quits():
    calls: list[str] = []
    tray = _make_tray(on_quit=lambda: calls.append("on_quit"))
    tray._icon = None
    tray._quit()
    assert calls == ["on_quit"]
