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
    # Round two — more of the same register.
    "delving", "hitherto", "aforementioned", "plethora",
    "quintessential", "multifaceted", "synergy", "synergies", "synergize",
    "spearhead", "spearheading", "showcase", "showcasing", "underpin",
    "underpinning", "facilitate", "facilitating", "cultivate", "cultivating",
    "empower", "empowering", "optimize", "optimizing", "resonate",
    "resonates", "amplify", "amplifying", "bolster", "bolstering",
    "encompass", "encompasses", "encompassing", "embark", "embarking",
    "unveil", "unveiling", "curate", "curated", "curating",
    "transformative", "revolutionize", "revolutionizing", "invaluable",
    "indelible", "unwavering", "steadfast", "compelling", "captivating",
    "boundless", "unparalleled", "ubiquitous", "burgeoning", "cornerstone",
    "catalyst", "roadmap", "actionable", "deliverable", "deliverables",
    "granular", "scalable", "turnkey", "best-in-class", "world-class",
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
    ("hedging", re.compile(r"\bit\s+is\s+important\s+to\s+(?:note|remember|understand)\b", re.I)),
    ("hedging", re.compile(r"\bplays?\s+a\s+(?:crucial|vital|key|pivotal|significant)\s+role\b", re.I)),
    ("hedging", re.compile(r"\bas\s+we\s+(?:can\s+see|(?:have\s+)?(?:seen|noted))\b", re.I)),
    ("throat-clearing", re.compile(r"\bin\s+conclusion\b", re.I)),
    ("throat-clearing", re.compile(r"\bin\s+summary\b", re.I)),
    ("throat-clearing", re.compile(r"\bin\s+the\s+(?:world|realm|age|era)\s+of\b", re.I)),
    ("throat-clearing", re.compile(r"\bfirst\s+and\s+foremost\b", re.I)),
    ("filler", re.compile(r"\ba\s+wide\s+(?:range|array|variety)\s+of\b", re.I)),
    ("filler", re.compile(r"\ba\s+testament\s+to\b", re.I)),
    ("filler", re.compile(r"\bthe\s+power\s+of\b", re.I)),
    ("filler", re.compile(r"\bunlock(?:s|ing)?\s+the\s+(?:power|potential|value)\b", re.I)),
    ("filler", re.compile(r"\btake\s+(?:it|things|your\s+\w+)\s+to\s+the\s+next\s+level\b", re.I)),
    ("cliche", re.compile(r"\bever[- ]evolving\b", re.I)),
    ("cliche", re.compile(r"\bever[- ]changing\b", re.I)),
    ("cliche", re.compile(r"\bcutting[- ]edge\b", re.I)),
    ("cliche", re.compile(r"\bgame[- ]chang(?:er|ing)\b", re.I)),
    ("cliche", re.compile(r"\bstate[- ]of[- ]the[- ]art\b", re.I)),
    ("cliche", re.compile(r"\bneedle\s+in\s+a\s+haystack\b", re.I)),
    ("cliche", re.compile(r"\btip\s+of\s+the\s+iceberg\b", re.I)),
    # Stiff comma-led transition openers LLMs stack at sentence starts. Anchored
    # to a trailing comma — that's the giveaway; ordinary use without one is left
    # alone. ("moreover"/"furthermore" as bare words are already in _VOCAB.)
    ("transition", re.compile(
        r"\b(?:additionally|however|consequently|thus|hence|notably|importantly|"
        r"ultimately|nevertheless|nonetheless|subsequently|firstly|secondly|"
        r"lastly|conversely|accordingly)\b\s*,", re.I)),
]

_VOCAB_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in sorted(_VOCAB, key=len, reverse=True))
    + r")\b", re.I)

# The rhythmic dash LLMs love — flagged whether it is spaced ("a — b") or tight
# ("word—word"). Any em dash (—) or en dash (–) counts; so does a spaced ASCII
# hyphen used as a dash ("a - b"). A normal hyphenated compound ("state-of-the-
# art", "well-tested") uses a TIGHT ascii hyphen and is NOT flagged.
_DASH_RE = re.compile(r"[—–]|(?<=\s)-(?=\s)")


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


def segments(text: str) -> list[tuple[bool, str]]:
    """Split ``text`` into ``(is_tell, chunk)`` runs that concatenate back to the
    original exactly, so a template can wrap the tell chunks in ``<mark>`` to
    highlight them in place. Returns markup-free data — escaping and styling stay
    in the template, as with :mod:`src.dashboard.textdiff`."""
    text = text or ""
    hits = find(text)
    if not hits:
        return [(False, text)] if text else []
    out: list[tuple[bool, str]] = []
    pos = 0
    for h in hits:
        if h.start > pos:
            out.append((False, text[pos:h.start]))
        out.append((True, text[h.start:h.end]))
        pos = h.end
    if pos < len(text):
        out.append((False, text[pos:]))
    return out
