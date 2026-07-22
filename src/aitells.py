"""Deterministic detector for the tells that give away AI-written prose.

One small, dependency-free module reused three ways:

  - the Humanize dashboard shows an "AI tells: N → M" score and lists what
    still remains, so the rewrite is legible instead of a black box;
  - :meth:`Cleaner.humanize_text` uses the score to decide whether a rewrite
    still needs a targeted second pass, and to keep only a pass that lowers it;
  - :mod:`scripts.eval_humanize` uses it as the tell-removal metric.

It is intentionally a HEURISTIC, not a classifier: it flags the specific,
mechanical tells the humanize prompt is asked to remove (see
``SYSTEM_PROMPTS["humanize_text_head"]`` in ``src/cleanup.py``), so the detector
and the instruction stay aligned. It never rewrites — it only points.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Hit:
    """One detected tell. ``start``/``end`` index into the original text so a UI
    can highlight the exact span; ``kind`` groups it; ``phrase`` is what matched."""
    start: int
    end: int
    kind: str
    phrase: str


# LLM-favourite vocabulary. Matched on word boundaries, case-insensitively.
# Kept in sync with the prompt's "LLM vocabulary" bullet — add to both.
_VOCAB = [
    "delve", "moreover", "furthermore", "crucial", "pivotal", "landscape",
    "realm", "tapestry", "testament to", "navigate", "navigating", "leverage",
    "leveraging", "robust", "seamless", "seamlessly", "underscore",
    "underscores", "foster", "fostering", "myriad", "holistic", "paradigm",
    "unprecedented", "profound", "profoundly", "meticulous", "meticulously",
    "intricate", "nuanced", "vibrant", "bustling", "beacon", "testament",
    "harness", "harnessing", "elevate", "elevating", "streamline",
    "streamlining", "endeavor", "endeavour", "utilize", "utilizing",
    "commendable", "noteworthy", "paramount", "realm of",
]

# Multi-word / phrasal tells. Each is a (kind, regex) pair. All case-insensitive.
_PHRASES: list[tuple[str, re.Pattern]] = [
    # The "not just X, it's Y" antithesis and its cousins.
    ("antithesis", re.compile(
        r"\b(?:it['’]?s|this\s+is|that['’]?s|there['’]?s)\s+not\s+"
        r"(?:just|only|merely)\b", re.I)),
    ("antithesis", re.compile(
        r"\b(?:is|are|was|were)n['’]?t\s+(?:just|only|merely)\b", re.I)),
    ("antithesis", re.compile(
        r"\bthis\s+is(?:n['’]?t| not)\s+about\b[^.]*\.\s*it['’]?s\s+about\b", re.I)),
    # Hedging / throat-clearing stacks.
    ("hedging", re.compile(
        r"\bit['’]?s\s+important\s+to\s+(?:note|remember|understand)\b", re.I)),
    ("hedging", re.compile(r"\b(?:it\s+is\s+worth\s+noting|worth\s+noting)\b", re.I)),
    ("hedging", re.compile(r"\bneedless\s+to\s+say\b", re.I)),
    ("hedging", re.compile(r"\bgenerally\s+speaking\b", re.I)),
    ("hedging", re.compile(r"\bin\s+many\s+ways\b", re.I)),
    ("throat-clearing", re.compile(r"\bwhen\s+it\s+comes\s+to\b", re.I)),
    ("throat-clearing", re.compile(r"\bat\s+the\s+end\s+of\s+the\s+day\b", re.I)),
    ("throat-clearing", re.compile(r"\bin\s+today['’]?s\s+(?:world|landscape|"
                                   r"digital\s+\w+|fast[- ]paced\s+\w+)", re.I)),
    ("cliche", re.compile(r"\bever[- ]evolving\b", re.I)),
    ("cliche", re.compile(r"\bever[- ]changing\b", re.I)),
    ("cliche", re.compile(r"\bcutting[- ]edge\b", re.I)),
    ("cliche", re.compile(r"\bgame[- ]chang(?:er|ing)\b", re.I)),
    ("cliche", re.compile(r"\bstate[- ]of[- ]the[- ]art\b", re.I)),
]

_VOCAB_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in sorted(_VOCAB, key=len, reverse=True))
    + r")\b", re.I)

# An em dash (—) or a spaced hyphen used as one — the rhythmic dash LLMs love.
# A normal hyphenated-word ("state-of-the-art") is NOT this; require the dash to
# be flanked by spaces or be a real em/en dash.
_DASH_RE = re.compile(r"\s+[—–]\s+|\s—|—\s|\s-\s")


def find(text: str) -> list[Hit]:
    """All tells in ``text``, sorted by position, de-overlapped (first wins)."""
    text = text or ""
    hits: list[Hit] = []
    for m in _VOCAB_RE.finditer(text):
        hits.append(Hit(m.start(), m.end(), "vocabulary", m.group(0)))
    for kind, rx in _PHRASES:
        for m in rx.finditer(text):
            hits.append(Hit(m.start(), m.end(), kind, m.group(0).strip()))
    for m in _DASH_RE.finditer(text):
        hits.append(Hit(m.start(), m.end(), "em-dash", m.group(0).strip() or "—"))

    hits.sort(key=lambda h: (h.start, -(h.end - h.start)))
    # Drop hits fully contained in an earlier (longer, or same-start) one so a
    # phrase and a vocabulary word inside it aren't double-counted.
    out: list[Hit] = []
    last_end = -1
    for h in hits:
        if h.start >= last_end:
            out.append(h)
            last_end = h.end
    return out


def score(text: str) -> int:
    """Number of distinct tells — the headline metric. 0 means "reads clean"."""
    return len(find(text))


def phrases(text: str, limit: int = 12) -> list[str]:
    """The distinct tell phrases present, lower-cased and de-duplicated, for a
    compact UI list ("still present: delve, moreover, seamless")."""
    seen: list[str] = []
    for h in find(text):
        p = h.phrase.lower()
        if p and p not in seen:
            seen.append(p)
        if len(seen) >= limit:
            break
    return seen
