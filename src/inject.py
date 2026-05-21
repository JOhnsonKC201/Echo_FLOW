"""Text injection at the focused cursor."""
from __future__ import annotations

import time
import sys

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


class Injector:
    def __init__(self, method: str = "paste", restore_clipboard: bool = True, trailing_space: bool = True):
        self.method = method
        self.restore_clipboard = restore_clipboard
        self.trailing_space = trailing_space

    def focused_title(self) -> str:
        return _focused_window_title()

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
