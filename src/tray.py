"""System tray icon — primary UI surface.

Replaces the terminal window. Lets the user pause/resume, switch language,
correct the last dictation, view history, and quit — all without a console.
"""
from __future__ import annotations

import threading
from typing import Callable

from PIL import Image, ImageDraw
import pystray

from . import log as wlog

_log = wlog.get("tray")


def _make_icon(active: bool = True, color: str = "ok") -> Image.Image:
    """Generate a 64x64 icon. Filled = active, hollow = paused."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    palette = {
        "ok":      (88, 199, 122),    # green
        "paused":  (200, 200, 200),   # grey
        "rec":     (220, 70, 70),     # red — recording
        "thinking": (100, 150, 240),  # blue — transcribing/cleaning
    }
    fill = palette.get(color, palette["ok"])
    # Microphone shape (rounded rect + stem)
    d.rounded_rectangle((22, 8, 42, 38), radius=10, fill=fill if active else None,
                        outline=fill, width=3)
    d.line((32, 38, 32, 52), fill=fill, width=4)
    d.line((22, 52, 42, 52), fill=fill, width=4)
    # Small arc beneath mic for "listening"
    d.arc((14, 28, 50, 56), start=20, end=160, fill=fill, width=3)
    return img


class TrayApp:
    """Owns the system tray icon and routes menu clicks to the main App."""

    def __init__(
        self,
        get_status: Callable[[], dict],
        on_pause_toggle: Callable[[], None],
        on_edit_last: Callable[[], None],
        on_open_history: Callable[[], None],
        on_open_graph: Callable[[], None],
        on_quit: Callable[[], None],
        on_open_review_queue: Callable[[], None] | None = None,
        on_pin_last: Callable[[], None] | None = None,
        on_toggle_prompt_mode: Callable[[], None] | None = None,
        get_prompt_mode_state: Callable[[], bool] | None = None,
        on_open_dashboard: Callable[[], None] | None = None,
        dashboard_hotkey_label: str | None = None,
    ):
        self.get_status = get_status
        self.on_pause_toggle = on_pause_toggle
        self.on_edit_last = on_edit_last
        self.on_open_history = on_open_history
        self.on_open_graph = on_open_graph
        self.on_open_review_queue = on_open_review_queue
        self.on_pin_last = on_pin_last
        self.on_toggle_prompt_mode = on_toggle_prompt_mode
        self.get_prompt_mode_state = get_prompt_mode_state
        self.on_open_dashboard = on_open_dashboard
        self.dashboard_hotkey_label = dashboard_hotkey_label
        self.on_quit = on_quit
        self._icon: pystray.Icon | None = None
        self._state = "ok"

    def _status_text(self, _item=None) -> str:
        try:
            s = self.get_status()
            phase = s.get("phase", "?")
            n = s.get("dictations", 0)
            q = s.get("avg_quality")
            q_str = f" • Q:{q:.0f}" if q is not None else ""
            return f"Status: {phase} • {n} dictations{q_str}"
        except Exception:
            return "Status: unknown"

    def _pause_label(self, _item=None) -> str:
        try:
            return "▶  Resume" if self.get_status().get("paused") else "⏸  Pause"
        except Exception:
            return "Pause / Resume"

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(self._status_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(self._pause_label, lambda i, item: self._safe(self.on_pause_toggle)),
            pystray.MenuItem("✏  Edit last dictation",
                             lambda i, item: self._safe(self.on_edit_last)),
            pystray.MenuItem("📋  Review queue (worst first)",
                             lambda i, item: self._safe(self.on_open_review_queue),
                             visible=lambda _it: self.on_open_review_queue is not None),
            pystray.MenuItem("📌  Pin last dictation",
                             lambda i, item: self._safe(self.on_pin_last),
                             visible=lambda _it: self.on_pin_last is not None),
            pystray.MenuItem(
                lambda _it: (f"🪟  Open Dashboard ({self.dashboard_hotkey_label})"
                             if self.dashboard_hotkey_label
                             else "🪟  Open Dashboard"),
                lambda i, item: self._safe(self.on_open_dashboard),
                visible=lambda _it: self.on_open_dashboard is not None),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda i, item: self._safe(self._quit)),
        )

    def _safe(self, fn, *args):
        try:
            fn(*args)
            self.refresh()
        except Exception as e:
            # The tray runs in the windowless daemon — print() goes nowhere, so
            # a menu action that silently failed left no trace. Log it.
            _log.warning("tray action %s failed: %s",
                         getattr(fn, "__name__", fn), e)

    def _quit(self):
        self.on_quit()
        if self._icon:
            self._icon.stop()

    def set_state(self, state: str):
        """state: ok | paused | rec | thinking"""
        self._state = state
        if self._icon:
            self._icon.icon = _make_icon(active=(state != "paused"), color=state)

    def refresh(self):
        if self._icon:
            self._icon.update_menu()

    def run(self):
        self._icon = pystray.Icon(
            "echo-flow",
            _make_icon(),
            "Echo Flow",
            menu=self._build_menu(),
        )
        # run() blocks; caller should put this in a thread
        self._icon.run()
