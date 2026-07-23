"""Deterministic "delete-first" pass for the humanizer.

Most of the improvement in de-AI-ing text is subtraction, not rewriting, and it
has to be deterministic: hand "make this concise" to a small local model and it
paraphrases instead of cutting, locking in the dead structure. So this module
removes the sentences that do no work — BEFORE the model sees the text:

  - throat-clearing / pure-hedge sentences ("It is important to note that …"),
  - empty-optimism closers ("… continues to evolve rapidly.", "The possibilities
    are endless."),
  - topic-announcement openers ("X has fundamentally transformed the landscape
    of Y.").

It is deliberately conservative. A sentence with a number, two or more proper
nouns, or real length is protected (it probably carries a fact), a lone sentence
is never cut, and a paragraph is never emptied — the point is to remove filler,
not content. Pure, model-free, and it reports exactly what it dropped so the UI
can show it.
"""
from __future__ import annotations

import re

# An optional leading connector, so "However, it is important to note …" is
# recognised as the throat-clearing sentence it is.
_LEAD = (r"(?:(?:however|moreover|furthermore|additionally|nevertheless|"
         r"nonetheless|still|yet|indeed|ultimately|of\s+course|in\s+fact|"
         r"that\s+said)[,\s]+)?")

# Sentence starts that are pure throat-clearing regardless of position.
_HEDGE_OPENER = re.compile(
    _LEAD + r"(?:it['’]?s|it\s+is)\s+(?:important|worth|essential|crucial|useful|"
    r"helpful|interesting|notable)\s+(?:to\s+(?:note|remember|mention|understand|"
    r"recognize|realize|point\s+out|acknowledge|keep\s+in\s+mind|bear\s+in\s+mind)"
    r"|noting)\b", re.I)
_HEDGE_PHRASE = re.compile(
    r"^(?:that\s+said|having\s+said\s+that|with\s+that\s+(?:said|in\s+mind)|"
    r"at\s+the\s+end\s+of\s+the\s+day|needless\s+to\s+say|as\s+(?:previously\s+)?"
    r"(?:mentioned|noted|stated|discussed)|in\s+other\s+words|simply\s+put|"
    r"to\s+put\s+it\s+simply)\b", re.I)

# Empty forward-looking optimism — dead when it closes a paragraph.
_OPTIMISM = re.compile(
    r"\b(?:continues?\s+to\s+(?:evolve|grow|advance|expand|develop|improve|"
    r"progress|flourish|mature)|(?:bright|promising|exciting|boundless)\s+"
    r"future|(?:exciting|endless|limitless|vast|boundless|immense|untapped)\s+"
    r"(?:possibilities|potential|opportunities|prospects)|(?:only\s+)?(?:just\s+)?"
    r"(?:begun|beginning|scratched\s+the\s+surface|getting\s+started)|here\s+to\s+"
    r"stay|remains?\s+to\s+be\s+seen|poised\s+to\b|no\s+signs?\s+of\s+slowing|"
    r"rapidly\s+(?:evolving|advancing|growing|changing|expanding)|(?:implications|"
    r"possibilities|potential)\s+(?:are|is)\s+(?:profound|vast|immense|enormous|"
    r"staggering|endless|exciting|far-reaching)|future\s+(?:is|looks|remains|"
    r"seems)\s+(?:bright|promising|exciting|wide\s+open))\b", re.I)

# Topic announcement — dead when it opens a multi-sentence paragraph.
_TOPIC_ANNOUNCE = re.compile(
    r"\b(?:has|have)\s+(?:fundamentally\s+|completely\s+|radically\s+|"
    r"profoundly\s+|utterly\s+|forever\s+)?(?:transformed|revolutionized|"
    r"revolutionised|reshaped|redefined|disrupted|reimagined)\s+(?:the\s+)?"
    r"(?:landscape|world|field|face|way|realm|domain|nature|fabric)\b", re.I)
_OPENER_WORLD = re.compile(
    r"^in\s+(?:the\s+(?:world|realm|age|era|domain|field)|today['’]?s\s+\w+)\s+of\b",
    re.I)

_DIGIT = re.compile(r"\d")
_CAP_WORD = re.compile(r"^[A-Z][a-zA-Z][a-zA-Z.]*$")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _protected(sentence: str) -> bool:
    """True when the sentence probably carries a fact and must not be cut:
    it has a number, two-plus proper nouns, or real length."""
    if _DIGIT.search(sentence):
        return True
    words = sentence.split()
    caps = sum(1 for w in words[1:] if _CAP_WORD.match(w) and w.lower() not in
               ("i", "i'm", "i've", "i'll"))
    return caps >= 2 or len(words) > 32


def _is_dead(sentence: str, i: int, n: int) -> bool:
    s = sentence.strip()
    if not s or n <= 1 or _protected(s):
        return False
    if _HEDGE_OPENER.match(s) or _HEDGE_PHRASE.match(s):
        return True
    if i == n - 1 and _OPTIMISM.search(s):          # closer
        return True
    if i == 0 and (_TOPIC_ANNOUNCE.search(s) or _OPENER_WORLD.match(s)):  # opener
        return True
    return False


def _trim_paragraph(para: str) -> tuple[str, list[str]]:
    sents = _SENT_SPLIT.split(para.strip())
    n = len(sents)
    kept, cuts = [], []
    for i, s in enumerate(sents):
        (cuts if _is_dead(s, i, n) else kept).append(s)
    if not kept:                                    # never empty a paragraph
        longest = max(sents, key=len)
        kept = [longest]
        cuts = [c for c in cuts if c != longest]
    return " ".join(kept), [c.strip() for c in cuts if c.strip()]


def trim(text: str) -> tuple[str, list[str]]:
    """Return ``(trimmed_text, cut_sentences)`` — dead sentences removed,
    paragraph structure preserved. Never raises."""
    text = text or ""
    if not text.strip():
        return text, []
    cuts: list[str] = []
    out: list[str] = []
    for chunk in re.split(r"(\n\s*\n)", text):      # keep blank-line separators
        if not chunk or re.fullmatch(r"\n\s*\n", chunk) or not chunk.strip():
            out.append(chunk)
            continue
        kept, para_cuts = _trim_paragraph(chunk)
        out.append(kept)
        cuts.extend(para_cuts)
    return "".join(out), cuts
