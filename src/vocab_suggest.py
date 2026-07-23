"""Pick which low-confidence words are worth suggesting for the dictionary.

Whisper hands back the words it was unsure about (`meta["low_conf_words"]`), but
most of those are ordinary words it merely hesitated on — not dictionary
material. The dictionary exists for *names and technical terms* the decoder
should be biased toward. So we keep a low-confidence word only when it:

  - survived into the final cleaned text (a word the user kept, not an artifact),
  - LOOKS like a term worth pinning — a proper noun, an internal-caps/digit
    token (FastAPI, node2vec, GPT4), not a plain lowercase common word,
  - isn't already known (already in the dictionary / mined vocabulary).

Pure and dependency-free so it is cheap on the dictation hot path and testable.
"""
from __future__ import annotations

import re

_WORD_RE = re.compile(r"[^\W\d_]+(?:[\w'’-]*[^\W_])?", re.UNICODE)
_STRIP_RE = re.compile(r"^[^\w]+|[^\w]+$", re.UNICODE)


def _core(token: str) -> str:
    return _STRIP_RE.sub("", token or "")


def _looks_like_term(tok: str) -> bool:
    """True for tokens the dictionary is actually for: proper nouns and
    technical/product names — not plain lowercase words."""
    core = _core(tok)
    # Drop a trailing possessive so "Kubernetes's" is judged as "Kubernetes".
    for ap in ("'s", "’s"):
        if core.lower().endswith(ap):
            core = core[:-2]
    if len(core) < 3 or not any(c.isalpha() for c in core):
        return False
    has_internal_caps = any(c.isupper() for c in core[1:])
    has_digit = any(c.isdigit() for c in core)
    is_proper = core[:1].isupper()
    return is_proper or has_internal_caps or has_digit


def filter_candidates(low_conf_words: list[tuple[str, float]],
                      cleaned_text: str,
                      known_terms: set[str]) -> list[tuple[str, float]]:
    """Return [(term, prob), ...] worth suggesting, best (lowest prob) per term.

    `known_terms` are compared case-insensitively. Words not present in
    `cleaned_text` (the pasted result) are dropped — a discarded mis-hearing is
    not a term to pin.
    """
    if not low_conf_words:
        return []
    known_lc = {k.lower() for k in (known_terms or set())}
    in_text = {m.group(0).lower() for m in _WORD_RE.finditer(cleaned_text or "")}
    best: dict[str, tuple[str, float]] = {}
    for raw_word, prob in low_conf_words:
        term = _core(raw_word)
        if not term or not _looks_like_term(term):
            continue
        lc = term.lower()
        if lc in known_lc or lc not in in_text:
            continue
        cur = best.get(lc)
        if cur is None or prob < cur[1]:
            best[lc] = (term, prob)
    return list(best.values())
