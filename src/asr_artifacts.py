"""Whisper's stock artifacts — the phrases it emits when it hears nothing.

Whisper was trained on captioned video, so on silence or noise it falls back to
the things captions habitually end with: "Thank you.", "Thanks for watching.",
a bare "you". Two guards already existed for this, byte-identical and
copy-pasted: one inside ``main._do_dictation`` and one in ``bridge`` (whose
comment says it "mirrors" main). This module is the single copy.

``strip_trailing`` is the calibration case. There the artifact is not the whole
utterance — the user really did read the sentence, and Whisper appended
"Thank you." to it. Scoring that as a misrecognition costs real accuracy points
for something the speaker never said, and feeding it to the pattern miner
teaches a correction out of noise.

Pure stdlib: ``calibration`` promises to stay import-light, and ``transcribe``
pulls in numpy.
"""
from __future__ import annotations

import re

# Exact contents of the two sets this module replaces — do not edit one without
# the other. `main` compares a whole utterance against this (dropping it when
# short); `strip_trailing` peels it off the end of a longer one.
HALLUCINATIONS = frozenset({
    "thank you.", "thanks for watching.", "thanks for watching!",
    "you", ".", "thank you", "thanks.", "bye.", "you're welcome.",
    "i'm sorry.", "thank you so much.",
})

_WORDS = re.compile(r"[A-Za-z0-9']+")


def _phrase_words(phrase: str) -> tuple[str, ...]:
    return tuple(_WORDS.findall(phrase.lower()))


# Longest first, so "thank you" wins over the bare "you" that is also in the set
# — otherwise "Thank you." peels to "Thank" and leaves a stray word behind.
#
# Leading `\s*` (not `[\s\W]*`) so the previous sentence keeps its full stop:
# "…settings folder. Thank you." must peel to "…settings folder.", not to
# "…settings folder". The `\b` guards stop "you" matching the tail of "bayou".
_TAIL_PATTERNS: list[re.Pattern] = [
    re.compile(r"\s*\b" + r"[\s\W]+".join(re.escape(w) for w in words) + r"\b[\s\W]*$",
               re.IGNORECASE)
    for words in sorted(
        {w for w in (_phrase_words(p) for p in HALLUCINATIONS) if w},
        key=len, reverse=True,
    )
]


def strip_trailing(text: str, max_peels: int = 3) -> str:
    """Remove trailing hallucination phrases from `text`.

    Peels repeatedly (Whisper sometimes stacks them) but never returns an empty
    string: if the whole utterance IS an artifact, that is `main`'s
    whole-utterance guard to make, not ours, and calibration would rather score
    a bad reading than silently invent a blank one.
    """
    out = (text or "").strip()
    for _ in range(max_peels):
        for pat in _TAIL_PATTERNS:
            stripped = pat.sub("", out).strip()
            if stripped == out:
                continue
            # The whole utterance is an artifact. Stop here rather than let a
            # shorter phrase nibble a word off it ("Thank you." -> "Thank").
            if not stripped:
                return out
            out = stripped
            break
        else:
            break
    return out
