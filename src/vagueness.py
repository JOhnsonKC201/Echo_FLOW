"""Deterministic detector for the vague, abstract claims that give AI text away —
the holes where a real fact should be.

The humanizer can strip the machine's tics, but it cannot invent what the writer
never said. "Significant improvements", "a number of challenges", "researchers
have shown" — each is a place the model gestured at a specific it doesn't know.
This module finds those spots and turns each into a QUESTION for the writer. It
never fills them in (that would be a bluff); it only asks.

Sibling of :mod:`src.aitells` in shape (Hole/find/segments), used by the Humanize
dashboard to show a "reads empty — make these concrete" checklist beside the
result. Pure, dependency-free, model-free.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Hole:
    """One vague claim. ``start``/``end`` index into the text so a UI can point at
    it; ``phrase`` is what matched; ``question`` is what to ask the writer."""
    start: int
    end: int
    kind: str
    phrase: str
    question: str


# (kind, question, regex, suppress_if_number)
# suppress_if_number: drop the hole when a digit appears in the SAME sentence —
# a concrete figure nearby means the claim isn't actually a hole ("grew
# significantly, up 42%").
_PATTERNS: list[tuple[str, str, re.Pattern, bool]] = [
    ("magnitude", "By how much? Give the number.", re.compile(
        r"\b(?:significant|substantial|considerable|dramatic|marked|notable|"
        r"remarkable|major|meaningful|size?able|huge|massive|drastic|vast|"
        r"tremendous)(?:ly)?\s+(?:improvement|increase|decrease|reduction|gain|"
        r"boost|drop|rise|growth|difference|impact|effect|amount|speed-?up|"
        r"jump|surge|decline)s?\b", re.I), True),
    ("magnitude", "By how much? Compared to what?", re.compile(
        r"\b(?:improved|increased|decreased|reduced|grew|rose|fell|dropped|"
        r"boosted|enhanced|doubled|tripled|halved)\b", re.I), True),
    ("magnitude", "By how much? Give the number.", re.compile(
        r"\b(?:much|far|way|significantly|considerably|substantially|markedly)\s+"
        r"(?:better|worse|faster|slower|stronger|weaker|higher|lower|greater|"
        r"smaller|more|less|cheaper|larger)\b", re.I), True),
    ("quantity", "How many, exactly?", re.compile(
        r"\ba\s+(?:number|variety|range|host|series|handful|couple|wealth|"
        r"multitude|plethora|myriad|slew|array)\s+of\b", re.I), True),
    ("quantity", "How many? Which ones?", re.compile(
        r"\b(?:numerous|various|several|multiple|countless|manifold|a\s+few|"
        r"a\s+lot\s+of|lots\s+of|plenty\s+of)\s+(?=[a-z])", re.I), True),
    ("citation", "Which study or source?", re.compile(
        r"\b(?:researchers?|scientists?|studies|experts?|research|evidence|"
        r"analysts?|the\s+literature|data)\s+(?:have\s+|has\s+)?(?:shown?|found|"
        r"suggests?|suggested|demonstrates?|demonstrated|reveals?|revealed|"
        r"indicates?|indicated|proves?|proven|proved|confirms?|confirmed|"
        r"reports?|reported)\b", re.I), False),
    ("time", "When, specifically?", re.compile(
        r"\b(?:recently|in\s+recent\s+years|in\s+recent\s+times|nowadays|these\s+"
        r"days|for\s+years|over\s+the\s+years|historically|traditionally|of\s+late)"
        r"\b", re.I), False),
    ("attribution", "By whom? Says who?", re.compile(
        r"\b(?:widely|generally|commonly|often|typically|universally)\s+"
        r"(?:believed|accepted|regarded|considered|known|used|thought|recognized|"
        r"acknowledged|understood)\b", re.I), False),
    ("comparison", "Beats what, and by how much?", re.compile(
        r"\b(?:outperform\w*|surpass\w*|exceed\w*|beats?\b)", re.I), True),
    ("vague-benefit", "How? By what measure?", re.compile(
        r"\b(?:powerful|robust|effective|efficient|scalable|seamless|versatile|"
        r"flexible|reliable|cutting-edge|state-of-the-art|world-class)\s+"
        r"(?:solution|approach|method|framework|tool|system|platform|technique|"
        r"model|pipeline|architecture)s?\b", re.I), False),
]

_SENT_SPLIT = re.compile(r"[.!?]+\s+")


def _sentence_bounds(text: str, pos: int) -> tuple[int, int]:
    """Start/end of the sentence containing ``pos`` (cheap, punctuation-based)."""
    start = 0
    for m in _SENT_SPLIT.finditer(text):
        if m.end() <= pos:
            start = m.end()
        else:
            return start, m.start()
    return start, len(text)


def find(text: str) -> list[Hole]:
    """All vague-claim holes in ``text``, sorted by position, de-overlapped."""
    text = text or ""
    holes: list[Hole] = []
    for kind, question, rx, suppress in _PATTERNS:
        for m in rx.finditer(text):
            if suppress:
                s, e = _sentence_bounds(text, m.start())
                if any(ch.isdigit() for ch in text[s:e]):
                    continue
            holes.append(Hole(m.start(), m.end(), kind,
                              m.group(0).strip(), question))
    holes.sort(key=lambda h: (h.start, -(h.end - h.start)))
    out: list[Hole] = []
    last_end = -1
    for h in holes:
        if h.start >= last_end:
            out.append(h)
            last_end = h.end
    return out


def count(text: str) -> int:
    return len(find(text))


def prompts(text: str, limit: int = 6) -> list[dict]:
    """The distinct holes as ``{phrase, question}`` for a UI checklist —
    de-duplicated on the (lower-cased phrase, question) pair, capped."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for h in find(text):
        key = (h.phrase.lower(), h.question)
        if key in seen:
            continue
        seen.add(key)
        out.append({"phrase": h.phrase, "question": h.question})
        if len(out) >= limit:
            break
    return out


def segments(text: str) -> list[tuple[bool, str]]:
    """``(is_hole, chunk)`` runs that concatenate back to ``text`` exactly, so a
    template can mark the vague spans. Markup-free (escaping stays in the view)."""
    text = text or ""
    holes = find(text)
    if not holes:
        return [(False, text)] if text else []
    out: list[tuple[bool, str]] = []
    pos = 0
    for h in holes:
        if h.start > pos:
            out.append((False, text[pos:h.start]))
        out.append((True, text[h.start:h.end]))
        pos = h.end
    if pos < len(text):
        out.append((False, text[pos:]))
    return out
