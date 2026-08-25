"""Deterministic filler removal for the LLM-free cleanup path.

Filler stripping only ever existed as an instruction inside the model prompts
("Remove filler words: um, uh, like, you know..."). That means the users who
need it most never got it: someone with no Ollama and no API key runs the
`learned` provider, which on a fresh install has no patterns to apply, so their
text reaches the cursor with every hesitation intact.

This module closes that gap with rules instead of a model. It is deliberately
timid, because the failure modes are asymmetric: leaving an "um" in is a small
annoyance, eating a real word is a bug the user cannot even see happening. So:

  - Hesitation sounds (um, uh, er, erm) are removed anywhere. Nobody dictates
    them on purpose.
  - Discourse markers (you know, I mean, sort of, basically) are removed only
    where they are unambiguously parenthetical: comma-delimited, or opening a
    sentence and followed by a comma.
  - "like" is removed only when comma-delimited on BOTH sides, because every
    other position is a real verb or preposition ("I like it", "looks like
    rain", "like this one").
  - A sentence is never emptied. If stripping would leave nothing, the original
    sentence is kept.

Like `deadweight.trim`, it reports exactly what it dropped so the dashboard can
show the user what happened rather than silently rewriting them.
"""
from __future__ import annotations

import re

# Hesitation noises. Safe to delete in any position: these are artifacts of
# speech, never content. Whisper transcribes them inconsistently ("um", "umm",
# "uh", "uhm"), hence the repeated-letter tolerance.
#
# Both edges are fenced by a word boundary at the use site. Without the
# TRAILING fence these eat the front of real words: "Ahmed" loses its "Ah" and
# becomes "Med", "Erlang" becomes "Lang". That is the exact failure this module
# is written to avoid, so the guard is not optional.
_HESITATION = r"(?:u+[hm]+|h+m+|e+r+m*|a+h+)"

# Markers that are filler ONLY when set off by punctuation. "basically" leading
# a clause is filler; "the basically identical file" is not, and neither is a
# "sort of" that modifies a noun ("a sort of blue").
_MARKERS = (
    "you know",
    "i mean",
    "sort of",
    "kind of",
    "basically",
    "actually",
    "literally",
    "obviously",
    "essentially",
)

_MARKER_ALT = "|".join(re.escape(m) for m in _MARKERS)

# 1. Hesitation anywhere, with any adjacent comma it brought with it. Fenced on
#    BOTH sides so it can only ever match a whole word.
_RE_HESITATION = re.compile(
    rf"(?<![\w']){_HESITATION}(?![\w'])\s*,?\s*", re.IGNORECASE
)

# 2. Marker in the middle of a clause, fenced by commas on both sides.
#    "it was, you know, fine" -> "it was fine"
_RE_MARKER_FENCED = re.compile(
    rf",\s*(?:{_MARKER_ALT})\s*,\s*", re.IGNORECASE
)

# 3. Marker opening a sentence and followed by a comma.
#    "Basically, we shipped it." -> "we shipped it."
_RE_MARKER_LEADING = re.compile(
    rf"(^|(?<=[.!?]\s))\s*(?:{_MARKER_ALT})\s*,\s*", re.IGNORECASE
)

# 4. "like" only when fenced by commas on both sides.
_RE_LIKE_FENCED = re.compile(r",\s*like\s*,\s*", re.IGNORECASE)

# Tidy-up after removal: doubled spaces, space before punctuation, a comma or
# period that lost the word it was attached to, a clause now opening with a
# comma.
_RE_SPACE = re.compile(r"[ \t]{2,}")
_RE_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?])")
_RE_DOUBLE_COMMA = re.compile(r",(\s*,)+")
_RE_LEADING_COMMA = re.compile(r"(^|(?<=[.!?]\s))\s*,\s*")


def _tidy(s: str) -> str:
    """Repair the punctuation and spacing a removal left behind."""
    s = _RE_DOUBLE_COMMA.sub(",", s)
    s = _RE_SPACE_BEFORE_PUNCT.sub(r"\1", s)
    s = _RE_LEADING_COMMA.sub(r"\1", s)
    s = _RE_SPACE.sub(" ", s)
    return s.strip()


def _split_sentences(text: str) -> list[str]:
    """Split on terminal punctuation, keeping the terminator with its sentence."""
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p for p in parts if p]


def strip(text: str) -> tuple[str, list[str]]:
    """Remove speech fillers. Returns (cleaned_text, dropped_fragments).

    Operates sentence by sentence so the never-empty-a-sentence guarantee is
    per sentence rather than per paragraph. When a sentence would be reduced to
    nothing (someone dictated only "um"), the original is kept untouched.
    """
    if not text or not text.strip():
        return text, []

    dropped: list[str] = []
    out_sentences: list[str] = []

    for sentence in _split_sentences(text):
        cleaned = sentence
        # Drops are staged per sentence: if the never-empty guard below rolls
        # the sentence back, its drops must roll back with it or the UI reports
        # removing words that are still there.
        staged: list[str] = []
        for pattern in (
            _RE_MARKER_FENCED,
            _RE_MARKER_LEADING,
            _RE_LIKE_FENCED,
            _RE_HESITATION,
        ):
            def _capture(m: re.Match) -> str:
                frag = m.group(0).strip().strip(",").strip()
                if frag:
                    staged.append(frag)
                # A fenced MARKER may have sat between genuine list items
                # ("we tested A, you know, B, and C"), so one comma goes back
                # or the list loses a separator. Fenced "like" is different:
                # a structural "like" never takes a comma immediately after it
                # ("a tool, like ripgrep, that is fast" does not match), so
                # both commas were filler and both go.
                if pattern is _RE_MARKER_FENCED:
                    return ", "
                if pattern is _RE_LIKE_FENCED:
                    return " "
                return ""

            cleaned = pattern.sub(_capture, cleaned)

        cleaned = _tidy(cleaned)
        # Nothing but punctuation left means we ate the whole sentence. Someone
        # who dictated only "um" gets their "um" back rather than an empty
        # paste.
        if not re.search(r"[A-Za-z0-9]", cleaned):
            out_sentences.append(sentence)
            continue
        # Recapitalize per sentence, not once over the whole result: removing
        # the opening filler of the SECOND sentence leaves it lowercase too.
        if sentence[:1].isupper() and cleaned[:1].islower():
            cleaned = cleaned[0].upper() + cleaned[1:]
        out_sentences.append(cleaned)
        dropped.extend(staged)

    result = " ".join(out_sentences).strip()
    if not result:
        return text, []
    return result, dropped
