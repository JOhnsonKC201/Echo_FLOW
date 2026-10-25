"""Text injection at the focused cursor."""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from pathlib import Path

import pyperclip

from . import hostos
from .hotkey import own_keystrokes


def _focused_window_title() -> str:
    """Title of the focused window, or "" when the OS will not say.

    Windows reads the Win32 foreground window. macOS asks AppKit: the window
    title once Screen Recording is granted, the application name otherwise.
    main.py caches this at hotkey-press time so the dictation hot path never
    pays for it.
    """
    return hostos.frontmost_title()


# A document filename embedded in a window title, e.g. "report.pdf - Adobe".
# The lookahead stops 'notes.mdx' matching as 'notes.md' (and 'budget.docm' as
# 'budget.doc'), which would resolve to a different file in the same folder.
_DOC_TOKEN = re.compile(r"([\w \-.()]+\.(?:pdf|txt|md|docx|doc))(?!\w)", re.IGNORECASE)


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
    # Path.cwd() raises when the working directory was deleted and
    # Path.home() when no home can be resolved; neither may escape.
    bases: list[Path] = []
    try:
        bases.append(Path.cwd())
    except Exception:
        pass
    try:
        home = Path.home()
        bases += [home / sub for sub in ("Documents", "Desktop", "Downloads")]
    except Exception:
        pass
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
        # Clipboard restore state, shared by back-to-back pastes. The user's
        # clipboard is saved once per burst and only the newest paste's timer
        # puts it back; otherwise a second paste inside the restore delay saves
        # the first dictation as "previous" and the user's clipboard is lost.
        self._clip_lock = threading.Lock()
        self._saved_clip: str | None = None
        self._restore_pending = False
        self._restore_gen = 0

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
        gen = None
        with self._clip_lock:
            if self.restore_clipboard and not self._restore_pending:
                try:
                    self._saved_clip = pyperclip.paste()
                except Exception:
                    self._saved_clip = None
            pyperclip.copy(text)
            if self.restore_clipboard and self._saved_clip is not None:
                self._restore_pending = True
                self._restore_gen += 1
                gen = self._restore_gen
        try:
            time.sleep(0.008)  # min wait for the clipboard to settle
            import pyautogui
            # _pause=False: pyautogui otherwise sleeps its default 0.1 s after
            # the keys are already sent, which only delayed every dictation.
            with own_keystrokes():
                pyautogui.hotkey(*hostos.paste_keys(), _pause=False)  # Ctrl+V, or Command+V on macOS
        finally:
            if gen is not None:
                # Restore in background so it doesn't block return. Started
                # even if the keystroke failed, so the pending flag clears.
                threading.Thread(target=lambda: self._restore_clipboard(gen),
                                 daemon=True).start()

    def _restore_clipboard(self, gen: int) -> None:
        # The target app reads the clipboard some time after Ctrl+V. 0.2 s
        # keeps the gap the old code had; restoring sooner pastes the old text.
        time.sleep(0.2)
        with self._clip_lock:
            if gen != self._restore_gen:
                return  # a newer paste owns the restore now
            self._restore_pending = False
            try:
                pyperclip.copy(self._saved_clip)
            except Exception:
                pass

    def _type(self, text: str):
        import pyautogui
        with own_keystrokes():
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
            with own_keystrokes():
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
            with own_keystrokes():
                pyautogui.hotkey(*parts)
            return True
        except Exception:
            return False
