"""Action item extraction from dictation text.

Pure regex + small heuristics. No LLM. Designed to be silent — extracted
items accumulate in the action_items table; only the editor UI surfaces them.

False positives are inevitable (regex can't really do intent). The mitigation
is that nothing pings the user about action items in real time — they only
show up when the user opens the dictation in the editor.
"""
from __future__ import annotations

import re


# Each pattern captures the "what to do" in a named group `text`.
# Patterns run on the cleaned text, which already has proper capitalization
# and punctuation, so we anchor on word boundaries and natural sentence breaks.
_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(?:TODO|FIXME|NOTE):\s*(?P<text>[^.!?]+)", re.IGNORECASE),
    re.compile(r"\bremind\s+me\s+to\s+(?P<text>[^.!?]+)", re.IGNORECASE),
    re.compile(r"\bI\s+(?:need|have|should|must|will)\s+to\s+(?P<text>[^.!?]+)", re.IGNORECASE),
    re.compile(r"\blet'?s\s+(?P<text>(?:do|fix|build|write|update|schedule|email|call|ping|reply)\s+[^.!?]+)", re.IGNORECASE),
    re.compile(r"\b(?P<text>(?:fix|build|update|write|send|schedule|email|reply\s+to)\s+the\s+[^.!?]+)", re.IGNORECASE),
]


# Skip patterns whose verb phrase points at trivial daily actions.
# "I need to go to bed", "I need to eat", "I have to pee" — not TODOs.
_BLOCKLIST = re.compile(
    r"\b(?:go\s+to\s+(?:bed|sleep|the\s+bathroom)|eat|pee|breathe|wake\s+up|"
    r"think|relax|chill|wait|stop\s+talking|be\s+quiet)\b",
    re.IGNORECASE,
)


def _clean_phrase(s: str) -> str:
    s = s.strip()
    # Trim trailing connectives and filler that often slip in
    s = re.sub(r"\s+(and|but|so|then|also)\s*$", "", s, flags=re.IGNORECASE)
    s = s.strip(" ,;-")
    return s


# --- Trailing voice commands (Phase 12) -------------------------------------
#
# Smallest Command Mode subset: a dictation that ends with one of a few
# explicit phrases ("press enter", "submit", "send it") fires a single key
# AFTER the cleaned text has been pasted. The trailing phrase is stripped
# from what gets pasted, so "send the email period press enter" pastes
# "Send the email." and then hits Enter.
#
# Gated by experimental.press_enter_command in config.yaml; off by default.
# Allowlist-only: nothing here can fire arbitrary keystrokes from voice.


_TRAILING_ENTER_RE = re.compile(
    r"""\s+                            # whitespace only — preserve sentence
                                       # punctuation that closes the payload
        (?:please\s+)?                 # optional politeness
        (?:press\s+enter
         | hit\s+enter
         | submit(?:\s+it)?
         | send\s+it
         | send\s+(?:the\s+)?message
         )
        [\s,.!?'"()-]*$                # eat trailing punct AFTER the command
    """,
    re.IGNORECASE | re.VERBOSE,
)


def detect_trailing_command(text: str) -> tuple[str, str] | None:
    """Inspect cleaned text for a trailing voice command.

    Returns (command_name, stripped_text) where stripped_text is what
    should actually be pasted (the command phrase removed). Returns None
    if no trailing command is present.

    Supported command_name values: "enter".
    """
    if not text:
        return None
    m = _TRAILING_ENTER_RE.search(text)
    if not m:
        return None
    stripped = text[: m.start()].rstrip()
    # Don't fire on a bare command-only dictation — too easy to mistrigger.
    if not stripped or len(stripped) < 2:
        return None
    return ("enter", stripped)


def extract_action_items(cleaned_text: str) -> list[str]:
    """Return a deduped, cleaned list of imperative phrases from the input.

    Each phrase is the captured "what to do" portion, not the full surrounding
    sentence — keeps the action_items table readable.
    """
    if not cleaned_text or len(cleaned_text) < 5:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for pat in _PATTERNS:
        for m in pat.finditer(cleaned_text):
            phrase = _clean_phrase(m.group("text"))
            if not phrase or len(phrase) < 3:
                continue
            if _BLOCKLIST.search(phrase):
                continue
            key = phrase.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(phrase)
    return found
