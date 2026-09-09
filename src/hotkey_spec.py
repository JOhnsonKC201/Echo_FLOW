"""Canonical hotkey grammar for transform bindings.

One spec, read by both the dashboard validator and the pynput registration
path. These used to be separate grammars and they drifted apart in both
directions: 'ctrl+shift' was refused by the validator even though pynput
registers it happily, while 'command+option+p' passed validation and was then
silently discarded at registration because the converter did not know the
macOS spellings. Validation and conversion now share these tables, so neither
can accept what the other throws away.

Not used by src/hotkey.py, which drives the live push-to-talk listener and has
its own pynput key mapping.
"""
from __future__ import annotations

# Alternate modifier spellings mapped to the canonical name. 'win' and
# 'command' are the same physical key as 'cmd'; 'option' is macOS for 'alt'.
ALIASES = {
    "control": "ctrl",
    "command": "cmd",
    "win": "cmd",
    "option": "alt",
}

# Modifiers are emitted in this order so that 'shift+ctrl+p' and 'ctrl+shift+p'
# canonicalize to one string. The uniqueness and conflict checks compare stored
# text, so without a fixed order the same chord reads as two different hotkeys.
MOD_ORDER = ("ctrl", "alt", "shift", "cmd")
MODIFIERS = frozenset(MOD_ORDER)

# pynput's HotKey.parse reads these as <space>, <enter>, <tab>, <esc>. The
# push-to-talk listener has always accepted them; transforms now do too.
NAMED_KEYS = frozenset({"space", "enter", "tab", "esc"})

# A chord with no terminal key needs at least this many modifiers held
# together. A single modifier would fire on essentially every keystroke.
MIN_BARE_MODIFIERS = 2

# pynput defines Key.f1 through Key.f24 on Windows and Linux.
MAX_FUNCTION_KEY = 24


def _function_key_number(part: str) -> int | None:
    """Return N for 'fN', or None when part is not spelled like a function key."""
    if len(part) < 2 or part[0] != "f" or not part[1:].isdigit():
        return None
    return int(part[1:])


def _is_key(part: str) -> bool:
    """True when part can serve as the terminal (non-modifier) key."""
    if len(part) == 1 and part.isalnum():
        return True
    if part in NAMED_KEYS:
        return True
    n = _function_key_number(part)
    return n is not None and 1 <= n <= MAX_FUNCTION_KEY


def _unknown_key_message(part: str, raw: str) -> str:
    n = _function_key_number(part)
    if n is not None:
        return (
            f"hotkey {raw!r} uses {part!r}, but function keys stop at "
            f"f{MAX_FUNCTION_KEY}"
        )
    named = ", ".join(sorted(NAMED_KEYS))
    return (
        f"hotkey {raw!r} does not recognize {part!r}. Use a letter, a digit, "
        f"f1 to f{MAX_FUNCTION_KEY}, or one of: {named}"
    )


def parse(combo: str) -> tuple[frozenset[str], str | None]:
    """'Ctrl + Shift' to (frozenset({'ctrl', 'shift'}), None).

    Returns (modifiers, key). key is None for a modifier-only chord such as
    'ctrl+shift', which the rest of Echo Flow uses for push-to-talk and which
    pynput registers without complaint.

    Raises ValueError whose message names the fix, not just the fault. These
    strings are shown verbatim to the user on the Transforms page.
    """
    raw = (combo or "").strip()
    if not raw:
        raise ValueError("hotkey is empty")
    parts = [p.strip().lower() for p in raw.split("+")]
    if any(not p for p in parts):
        raise ValueError(f"hotkey {raw!r} has a blank part. Check the + signs.")

    mods: list[str] = []
    key: str | None = None
    last = len(parts) - 1
    for i, part in enumerate(parts):
        canon = ALIASES.get(part, part)
        if canon in MODIFIERS:
            if canon in mods:
                raise ValueError(f"hotkey {raw!r} repeats the {canon!r} modifier")
            mods.append(canon)
            continue
        # Anything that is not a modifier has to be the single terminal key.
        if i != last:
            raise ValueError(
                f"{part!r} is not a modifier, so it has to come last in {raw!r}"
            )
        if not _is_key(part):
            raise ValueError(_unknown_key_message(part, raw))
        key = part

    if key is None and len(mods) < MIN_BARE_MODIFIERS:
        raise ValueError(
            f"hotkey {raw!r} needs a key or a second modifier. "
            f"Try {raw}+1, or ctrl+alt."
        )
    # A bare letter or digit would fire while typing prose. Function keys are
    # safe alone, and config.yaml already advertises 'f9' as a valid hotkey.
    if key is not None and not mods and _function_key_number(key) is None:
        raise ValueError(
            f"hotkey {raw!r} needs at least one modifier. Try 'ctrl+alt+{key}'."
        )
    return frozenset(mods), key


def canonical(combo: str) -> str:
    """Normalized storage form: modifiers in MOD_ORDER, then the key.

    Both 'shift+ctrl+p' and 'Ctrl + Shift + P' come back as 'ctrl+shift+p'.
    """
    mods, key = parse(combo)
    parts = [m for m in MOD_ORDER if m in mods]
    if key:
        parts.append(key)
    return "+".join(parts)


def to_pynput(combo: str) -> str:
    """'ctrl+alt+p' to '<ctrl>+<alt>+p' for pynput.keyboard.GlobalHotKeys.

    Raises ValueError on anything GlobalHotKeys could not register, so a bad
    binding fails loudly here rather than being skipped at registration time.
    """
    mods, key = parse(combo)
    out = [f"<{m}>" for m in MOD_ORDER if m in mods]
    if key:
        # Single characters go through bare; named and function keys bracketed.
        out.append(key if len(key) == 1 else f"<{key}>")
    return "+".join(out)


def canonical_or_none(combo: str) -> str | None:
    """canonical() for values that may be bracketed or hand-edited.

    config.yaml stores dashboard.open_hotkey as '<ctrl>+<cmd>'. Returns None
    instead of raising, because reserved-hotkey lookups read a config the user
    may have edited by hand and must never take the page down.
    """
    text = (combo or "").strip()
    if not text:
        return None
    try:
        return canonical(text.replace("<", "").replace(">", ""))
    except ValueError:
        return None
