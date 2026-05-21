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
