"""Global hotkey listener (push-to-talk or toggle)."""
from __future__ import annotations

from pynput import keyboard


def _parse_combo(combo: str):
    """'ctrl+alt+space' -> set of pynput keys."""
    mapping = {
        "ctrl": keyboard.Key.ctrl,
        "control": keyboard.Key.ctrl,
        "alt": keyboard.Key.alt,
        "shift": keyboard.Key.shift,
        "cmd": keyboard.Key.cmd,
        "win": keyboard.Key.cmd,
        "space": keyboard.Key.space,
        "enter": keyboard.Key.enter,
        "tab": keyboard.Key.tab,
        "esc": keyboard.Key.esc,
    }
    keys = set()
    for part in combo.lower().split("+"):
        part = part.strip()
        if part in mapping:
            keys.add(mapping[part])
        elif part.startswith("f") and part[1:].isdigit():
            # pynput only defines f1..f20. getattr on f0/f25/f99 raises
            # AttributeError; convert to the intended ValueError so callers
            # catching ValueError surface a friendly "bad hotkey" message.
            fkey = getattr(keyboard.Key, part, None)
            if fkey is None:
                raise ValueError(f"Unknown key: {part}")
            keys.add(fkey)
        elif len(part) == 1:
            keys.add(keyboard.KeyCode.from_char(part))
        else:
            raise ValueError(f"Unknown key: {part}")
    return keys


class HotkeyListener:
    """Fires on_press_combo when combo is fully held; on_release_combo when any key released.

    Optional `veto_keys`: any key in this set, when held with the main combo,
    suppresses activation. If `on_veto` is provided and the listener is
    currently active when a veto key is pressed, on_veto fires (used to abort
    in-progress recordings when a longer combo is forming).
    """

    def __init__(self, combo: str, mode: str, on_activate, on_deactivate=None,
                 veto_keys: str | None = None, on_veto=None):
        self.combo = _parse_combo(combo)
        self.veto_keys = _parse_combo(veto_keys) if veto_keys else set()
        self.on_veto = on_veto
        self.mode = mode  # "hold" or "toggle"
        self.on_activate = on_activate
        self.on_deactivate = on_deactivate
        self._pressed = set()
        self._active = False

    def _norm(self, key):
        # Normalize l/r modifiers
        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            return keyboard.Key.ctrl
        if key in (keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt_gr):
            return keyboard.Key.alt
        if key in (keyboard.Key.shift_l, keyboard.Key.shift_r):
            return keyboard.Key.shift
        return key

    def _on_press(self, key):
        k = self._norm(key)
        self._pressed.add(k)
        # Veto: a "longer" combo is forming. Cancel if active, skip otherwise.
        if k in self.veto_keys:
            if self._active:
                if self.on_veto:
                    try:
                        self.on_veto()
                    except Exception as e:
                        import logging
                        logging.getLogger("wispr.hotkey").warning("veto callback failed: %s", e)
                self._active = False
            return
        # Don't activate while any veto key is already held.
        if self.veto_keys & self._pressed:
            return
        if self.combo.issubset(self._pressed) and not self._active:
            self._active = True
            if self.mode == "toggle":
                self.on_activate()
            else:
                self.on_activate()

    def _on_release(self, key):
        k = self._norm(key)
        self._pressed.discard(k)
        if self._active and not self.combo.issubset(self._pressed):
            if self.mode == "hold":
                self._active = False
                if self.on_deactivate:
                    self.on_deactivate()
            elif self.mode == "toggle":
                # toggle ignores release — caller handles auto-stop
                self._active = False

    def run(self):
        with keyboard.Listener(on_press=self._on_press, on_release=self._on_release) as listener:
            listener.join()
