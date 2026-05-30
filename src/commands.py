"""Phase 13 — Command Mode: regex-first voice-command intent classifier.

Activated only when the dictation starts with the command prefix word
(default: "computer", configurable via `experimental.command_prefix`).
This keeps regular dictation safe — "select all the rows we discussed"
never fires Ctrl+A unless prefixed with "Computer, select all".

Allowlist-only: every supported command resolves to one entry in
COMMAND_TABLE. Each entry is either:
  ("key", <pyautogui-key>) — single key, e.g. "enter", "escape", "tab"
  ("hotkey", <combo>)      — combo, e.g. "ctrl+c", "ctrl+shift+t"

No "text" / "type" / "shell" actions. Voice cannot ever produce arbitrary
keystrokes. If a command isn't on the allowlist, the dictation is dropped
with a notify-style warning — never silently typed.
"""
from __future__ import annotations

import re
from typing import Iterable


# (regex, action_type, action_value, human_label)
_COMMANDS: list[tuple[re.Pattern[str], str, str, str]] = [
    # Navigation
    (re.compile(r"^scroll\s+(?:down|down\s+more)\b", re.I), "key", "pagedown", "scroll down"),
    (re.compile(r"^scroll\s+up\b", re.I),                    "key", "pageup",   "scroll up"),
    (re.compile(r"^page\s+down\b", re.I),                    "key", "pagedown", "page down"),
    (re.compile(r"^page\s+up\b", re.I),                      "key", "pageup",   "page up"),
    (re.compile(r"^go\s+(?:to\s+)?(?:the\s+)?top\b", re.I),  "hotkey", "ctrl+home", "jump to top"),
    (re.compile(r"^go\s+(?:to\s+)?(?:the\s+)?(?:bottom|end)\b", re.I),
                                                              "hotkey", "ctrl+end",  "jump to bottom"),
    (re.compile(r"^go\s+back\b", re.I),                       "hotkey", "alt+left",  "go back"),
    (re.compile(r"^go\s+forward\b", re.I),                    "hotkey", "alt+right", "go forward"),

    # Selection / clipboard
    (re.compile(r"^select\s+all\b", re.I),                    "hotkey", "ctrl+a", "select all"),
    (re.compile(r"^copy(?:\s+that)?\b", re.I),                "hotkey", "ctrl+c", "copy"),
    (re.compile(r"^cut(?:\s+that)?\b", re.I),                 "hotkey", "ctrl+x", "cut"),
    (re.compile(r"^paste(?:\s+it|\s+that)?\b", re.I),         "hotkey", "ctrl+v", "paste"),
    (re.compile(r"^undo(?:\s+that)?\b", re.I),                "hotkey", "ctrl+z", "undo"),
    (re.compile(r"^redo\b", re.I),                            "hotkey", "ctrl+y", "redo"),

    # File / editor
    (re.compile(r"^save\b", re.I),                            "hotkey", "ctrl+s", "save"),
    (re.compile(r"^find\b", re.I),                            "hotkey", "ctrl+f", "find"),
    (re.compile(r"^new\s+tab\b", re.I),                       "hotkey", "ctrl+t", "new tab"),
    (re.compile(r"^close\s+tab\b", re.I),                     "hotkey", "ctrl+w", "close tab"),
    (re.compile(r"^reopen\s+tab\b", re.I),                    "hotkey", "ctrl+shift+t", "reopen tab"),

    # Single keys
    (re.compile(r"^(?:press\s+)?enter\b", re.I),              "key", "enter",  "press enter"),
    (re.compile(r"^(?:press\s+)?escape\b", re.I),             "key", "escape", "press escape"),
    (re.compile(r"^(?:press\s+)?tab\b", re.I),                "key", "tab",    "press tab"),
    (re.compile(r"^(?:press\s+)?(?:back\s*space|delete\s+that)\b", re.I),
                                                              "key", "backspace", "backspace"),
]


# Allowlist of every key/combo that voice can ever fire. Used to gate
# user-defined commands (out of scope for MVP but the gate is in place).
SAFE_KEYS = {
    "enter", "escape", "tab", "backspace", "pagedown", "pageup",
    "home", "end", "up", "down", "left", "right",
}

SAFE_MODIFIERS = {"ctrl", "shift", "alt", "win"}
SAFE_HOTKEY_LETTERS = set("abcdefghijklmnopqrstuvwxyz0123456789") | {
    "home", "end", "left", "right", "up", "down",
}


def is_safe_hotkey(combo: str) -> bool:
    """True if `combo` is composed only of modifiers + one safe key."""
    if not combo:
        return False
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if len(parts) < 2:
        return False
    *mods, key = parts
    if not all(m in SAFE_MODIFIERS for m in mods):
        return False
    return key in SAFE_HOTKEY_LETTERS


def strip_prefix(text: str, prefix_word: str = "computer") -> str | None:
    """If `text` begins with the prefix word (and optional comma/space),
    return the remainder; otherwise None.

    Match is case-insensitive and tolerant of trailing punctuation:
        "Computer, select all" → "select all"
        "computer scroll down"  → "scroll down"
    """
    if not text or not prefix_word:
        return None
    pat = re.compile(rf"^\s*{re.escape(prefix_word)}[\s,.:;-]+", re.IGNORECASE)
    m = pat.match(text)
    if not m:
        return None
    return text[m.end():].strip()


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance — small, dependency-free."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def strip_prefix_fuzzy(text: str, prefix_word: str, max_dist: int = 2) -> str | None:
    """Like strip_prefix, but tolerant of a MIS-HEARD wake word.

    Speech-to-text routinely mangles an uncommon prefix ("jarvis" → "Zalvis",
    "Jervis", "Travis"). An exact match would then drop the command silently.
    This compares the first spoken token to the prefix by edit distance and,
    if close enough, returns the remainder.

    Conservative by design: only for prefixes of 4+ letters, only a first token
    of a similar length, and distance strictly less than the prefix length.
    Callers must still gate on the remainder actually resolving to an action —
    a fuzzy hit alone must never swallow plain dictation.
    """
    exact = strip_prefix(text, prefix_word)
    if exact is not None:
        return exact
    pw = (prefix_word or "").strip().lower()
    if len(pw) < 4 or " " in pw:
        # Single-token fuzzy only — a multi-word prefix ("hey echo") is handled
        # exactly above; fuzzy-matching just its first word would mis-strip.
        return None
    m = re.match(r"^\s*([A-Za-z']+)[\s,.:;\-]+(.+)$", text or "")
    if not m:
        return None
    first = m.group(1).lower()
    rest = m.group(2).strip()
    if not rest or abs(len(first) - len(pw)) > 2:
        return None
    dist = _edit_distance(first, pw)
    if dist <= max_dist and dist < len(pw):
        return rest
    return None


def classify(command_text: str) -> tuple[str, str, str] | None:
    """Match the prefix-stripped command body against the allowlist.

    Returns (action_type, action_value, human_label) on a hit, else None.
    """
    if not command_text:
        return None
    body = command_text.strip()
    for pat, action_type, action_value, label in _COMMANDS:
        if pat.search(body):
            # Defense in depth — confirm allowlist for the hotkey class.
            if action_type == "hotkey" and not is_safe_hotkey(action_value):
                continue
            if action_type == "key" and action_value not in SAFE_KEYS:
                continue
            return (action_type, action_value, label)
    return None


def list_supported() -> list[str]:
    """For the dashboard's experimental panel: every label, ordered."""
    return [label for _pat, _t, _v, label in _COMMANDS]
