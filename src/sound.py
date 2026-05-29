"""Audible feedback for recording start/stop/error.

Plays through the normal audio output (NOT the legacy PC speaker, which
is silent on most modern machines). Two backends, tried in order:

1. winsound.PlaySound with a Windows system alias (SystemAsterisk etc.) —
   always audible because it uses the OS event sounds you can hear.
2. winsound.Beep (legacy, often inaudible) — only as last resort.

Non-blocking — runs in a background thread.
"""
from __future__ import annotations

import sys
import threading

from . import log as wlog
_log = wlog.get("sound")


# Map our event names to Windows system sound aliases.
# These always play through the regular speakers, controlled by the
# "System" channel in the Windows volume mixer.
_ALIAS_MAP = {
    "start": "SystemAsterisk",     # short crisp ping (Information sound)
    "stop":  "SystemDefault",      # softer default beep (acknowledges release)
    "error": "SystemHand",         # error/stop sound
    "ready": "SystemNotification", # gentle chime for "daemon ready"
}


def _resolve_wav(spec: str) -> str | None:
    """Try to resolve a .wav name to a real path.
    Accepts: full path, relative path, or bare filename (looks in C:\\Windows\\Media)."""
    import os
    path = spec.replace("/", "\\")
    if os.path.exists(path):
        return path
    # Try Windows Media folder for bare names like "ding.wav"
    candidate = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Media", path)
    if os.path.exists(candidate):
        return candidate
    return None


def _play_alias_or_file(spec: str) -> bool:
    """Play a Windows system alias OR a .wav file (path or bare name)."""
    if sys.platform != "win32" or not spec:
        return False
    try:
        import winsound
        # If it looks like a WAV reference, resolve and play as file
        if spec.lower().endswith(".wav") or "/" in spec or "\\" in spec or ":" in spec:
            path = _resolve_wav(spec)
            if path:
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return True
            _log.warning("sound file not found: %s", spec)
            return False
        # Otherwise treat as a system sound alias
        winsound.PlaySound(
            spec, winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
        return True
    except Exception as e:
        _log.debug("PlaySound(%s) failed: %s", spec, e)
        return False


def _play_beep(freq: int, duration_ms: int) -> bool:
    """Legacy fallback. Often silent on modern Windows — that's expected."""
    if sys.platform != "win32":
        return False
    try:
        import winsound
        winsound.Beep(freq, duration_ms)
        return True
    except Exception as e:
        _log.debug("Beep failed: %s", e)
        return False


def preview(alias: str) -> bool:
    """Play an arbitrary alias/WAV once, for the Settings 'Test' button.

    Unlike play(), this ignores the sound.enabled flag — it's an explicit,
    user-initiated audition. Resolution matches _play_alias_or_file: a Windows
    system alias (SystemAsterisk), a bare WAV name resolved against
    C:\\Windows\\Media (ding.wav), or a full path. Returns True if a backend
    accepted it. PlaySound runs async, so this returns promptly.
    """
    spec = (alias or "").strip()
    if not spec:
        return False
    return _play_alias_or_file(spec)


# Curated catalog for the Settings sound picker — (value, label) pairs.
# Windows Media WAVs ship on virtually every Win10/11 install; system aliases
# always resolve. Users can still type any other WAV name or full path.
SOUND_CHOICES: list[tuple[str, str]] = [
    # Crisp "listening" cues — good for the start sound.
    ("Speech On.wav",               "Speech On — crisp “listening” chirp"),
    ("Speech Sleep.wav",            "Speech Sleep — soft down-note"),
    ("Speech Off.wav",              "Speech Off — “stopped listening”"),
    ("Speech Misrecognition.wav",   "Speech Misrecognition — gentle buzz"),
    ("ding.wav",                    "Ding — short, neutral"),
    ("chimes.wav",                  "Chimes — soft three-note"),
    ("chord.wav",                   "Chord — mellow"),
    ("notify.wav",                  "Notify — classic"),
    ("tada.wav",                    "Tada — celebratory"),
    ("recycle.wav",                 "Recycle — quick swoosh"),
    # Modern Windows 10/11 notification set.
    ("Windows Notify.wav",                  "Windows Notify"),
    ("Windows Notify System Generic.wav",   "Windows Notify (generic)"),
    ("Windows Notify Messaging.wav",        "Windows Notify (messaging)"),
    ("Windows Notify Calendar.wav",         "Windows Notify (calendar)"),
    ("Windows Notify Email.wav",            "Windows Notify (email)"),
    ("Windows Foreground.wav",              "Windows Foreground"),
    ("Windows Background.wav",              "Windows Background — subtle"),
    ("Windows Ding.wav",                    "Windows Ding"),
    ("Windows Default.wav",                 "Windows Default"),
    ("Windows Message Nudge.wav",           "Windows Nudge"),
    ("Windows Print complete.wav",          "Windows Print complete"),
    ("Windows Proximity Notification.wav",  "Windows Proximity"),
    ("Windows Unlock.wav",                  "Windows Unlock"),
    ("Windows Logon.wav",                   "Windows Logon"),
    ("Windows Battery Low.wav",             "Windows Battery Low"),
    ("Alarm01.wav",                         "Alarm 01"),
    ("Alarm02.wav",                         "Alarm 02"),
    ("Ring01.wav",                          "Ring 01"),
    # Always-resolvable system aliases.
    ("SystemAsterisk",      "System Asterisk (alias)"),
    ("SystemNotification",  "System Notification (alias)"),
    ("SystemExclamation",   "System Exclamation (alias)"),
    ("SystemDefault",       "System Default (alias)"),
    ("SystemHand",          "System Hand — error (alias)"),
    ("SystemQuestion",      "System Question (alias)"),
]


def list_choices() -> list[dict]:
    """Curated sound options for the Settings picker.

    Each item: {"value", "label", "available"}. `available` is True for system
    aliases and for Media WAVs that resolve on THIS machine, so the UI can
    de-emphasize ones the user doesn't have. Non-Windows → only aliases marked
    available (the picker still lists everything; users type freely).
    """
    out: list[dict] = []
    for value, label in SOUND_CHOICES:
        is_alias = not value.lower().endswith(".wav")
        available = is_alias or (_resolve_wav(value) is not None)
        out.append({"value": value, "label": label, "available": available})
    return out


def play(kind: str, cfg: dict | None = None) -> None:
    """Play a named feedback sound. kind: 'start' | 'stop' | 'error' | 'ready'."""
    cfg = cfg or {}
    if not cfg.get("enabled", True):
        return

    alias_override = cfg.get(f"{kind}_alias")        # let user pick any alias
    alias = alias_override or _ALIAS_MAP.get(kind)

    def _run():
        if alias and _play_alias_or_file(alias):
            _log.debug("played %s for %s", alias, kind)
            return
        # Fallback to Beep if alias/file path failed
        freq = cfg.get(f"{kind}_freq", 1000)
        dur = cfg.get(f"{kind}_ms", 60)
        _play_beep(freq, dur)

    threading.Thread(target=_run, daemon=True).start()
