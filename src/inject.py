"""Text injection at the focused cursor."""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import pyperclip


def _focused_window_title() -> str:
    if sys.platform != "win32":
        return ""
    try:
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        return win32gui.GetWindowText(hwnd) or ""
    except Exception:
        return ""


# A document filename embedded in a window title, e.g. "report.pdf - Adobe".
_DOC_TOKEN = re.compile(r"([\w \-.()]+\.(?:pdf|txt|md|docx|doc))", re.IGNORECASE)


def _focused_document_path() -> str | None:
    """Best-effort path of the foreground window's document. Returns None when
    it can't resolve — callers MUST tolerate None. Never raises; only runs when
    an action actually fires, so it's off the dictation hot path.

    Heuristic: pull a filename token out of the window title and look for it in
    the usual places (cwd, Documents, Desktop, Downloads). Deliberately
    conservative — a guess that resolves to a real file, or nothing.
    """
    title = _focused_window_title()
    if not title:
        return None
    m = _DOC_TOKEN.search(title)
    if not m:
        return None
    name = m.group(1).strip()
    bases = [Path.cwd()]
    home = Path.home()
    bases += [home / sub for sub in ("Documents", "Desktop", "Downloads")]
    for base in bases:
        try:
            p = base / name
            if p.is_file():
                return str(p)
        except Exception:
            continue
    return None


class Injector:
    def __init__(self, method: str = "paste", restore_clipboard: bool = True, trailing_space: bool = True):
        self.method = method
        self.restore_clipboard = restore_clipboard
        self.trailing_space = trailing_space

    def focused_title(self) -> str:
        return _focused_window_title()

    def focused_document_path(self) -> str | None:
        """Best-effort path of the foreground document (Phase 14 Action Mode).
        Returns None when unresolved; only called when an action fires."""
        return _focused_document_path()

    def inject(self, text: str):
        if not text:
            return
        if self.trailing_space and not text.endswith((" ", "\n")):
            text = text + " "
        if self.method == "type":
            self._type(text)
        else:
            self._paste(text)

    def _paste(self, text: str):
        prev = None
        if self.restore_clipboard:
            try:
                prev = pyperclip.paste()
            except Exception:
                prev = None
        pyperclip.copy(text)
        time.sleep(0.008)  # min wait for clipboard to settle on Windows
        import pyautogui
        pyautogui.hotkey("ctrl", "v")
        if self.restore_clipboard and prev is not None:
            # Restore in background so it doesn't block return
            import threading
            def _restore():
                time.sleep(0.1)
                try:
                    pyperclip.copy(prev)
                except Exception:
                    pass
            threading.Thread(target=_restore, daemon=True).start()

    def _type(self, text: str):
        import pyautogui
        pyautogui.typewrite(text, interval=0.005)

    def send_key(self, key: str) -> bool:
        """Fire a single key. Phase 12 — used for trailing voice commands.

        `key` is a pyautogui key name ("enter", "tab", "escape", ...). Returns
        True on success, False if anything failed. Caller must ensure the key
        is on its own allowlist; this method does no policy enforcement.
        """
        try:
            import pyautogui
            # Small grace period so the prior paste settles before the key.
            time.sleep(0.05)
            pyautogui.press(key)
            return True
        except Exception:
            return False

    def send_hotkey(self, combo: str) -> bool:
        """Fire a key combo like "ctrl+c" or "ctrl+shift+t". Phase 13.

        Caller must allowlist the combo upstream — this method does no
        policy enforcement (consistent with send_key).
        """
        if not combo:
            return False
        parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
        if not parts:
            return False
        try:
            import pyautogui
            time.sleep(0.05)
            pyautogui.hotkey(*parts)
            return True
        except Exception:
            return False
