"""Tiny phonetic gate for the learned-pattern miner.

The multi-word substitution learner (`learn._diff_ngram_pairs`) must tell a
genuine ASR *mishearing* ("note to vec" -> "node2vec", "let us" -> "Lattice")
apart from an ordinary LLM *rewrite* ("the weather is nice" -> "let's ship it").
The tell is that a mishearing SOUNDS the same; a rewrite does not. So we reduce
each side to a phonetic key (Metaphone) and compare the keys.

Metaphone (Lawrence Philips, 1990) collapses English spelling to its consonant
sounds — NODE and NOTE both key to "NT", PHONE to "FN" — which is exactly the
signal we want: same sound, different spelling. Vendored (no dependency) and
pure, so it is cheap and unit-testable.

The learner only needs `phonetic_similar`; `metaphone` / `phonetic_key` are
exposed for testing and reuse.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

_VOWELS = frozenset("AEIOU")
_ALPHA_RE = re.compile(r"[A-Za-z]+")


def metaphone(word: str) -> str:
    """Metaphone phonetic key for a single word (uppercase letters only).

    A compact implementation of the classic algorithm — enough of the rules to
    be discriminative for ASR error detection, not a spec-exact port. Returns
    "" for input with no letters.
    """
    w = "".join(ch for ch in (word or "").upper() if "A" <= ch <= "Z")
    if not w:
        return ""

    # --- Initial-letter exceptions: silent leading consonants -----------------
    if w[:2] in ("AE", "GN", "KN", "PN", "WR"):
        w = w[1:]
    elif w[:1] == "X":
        w = "S" + w[1:]
    elif w[:2] == "WH":
        w = "W" + w[2:]

    key: list[str] = []
    n = len(w)

    def at(i: int) -> str:
        return w[i] if 0 <= i < n else ""

    i = 0
    # A leading vowel is the only vowel that survives.
    if w and w[0] in _VOWELS:
        key.append(w[0])
        i = 1

    while i < n:
        c = w[i]
        prev = at(i - 1)
        nxt = at(i + 1)
        nxt2 = at(i + 2)

        # Skip a doubled consonant (except C, which carries CC sounds).
        if c == prev and c != "C":
            i += 1
            continue

        if c in _VOWELS:
            i += 1
            continue  # non-initial vowels are dropped

        if c == "B":
            # Silent B at end after M ("dumb", "thumb").
            if not (i == n - 1 and prev == "M"):
                key.append("B")
        elif c == "C":
            if nxt == "I" and nxt2 == "A":
                key.append("X")            # -CIA- -> X
            elif nxt == "H":
                key.append("K" if prev == "S" else "X")  # SCH->K, else CH->X
                i += 1
            elif nxt in ("I", "E", "Y"):
                if prev != "S":            # SCE/SCI already an S sound
                    key.append("S")
            else:
                key.append("K")
        elif c == "D":
            if nxt == "G" and nxt2 in ("E", "I", "Y"):
                key.append("J")            # -DGE- -> J
                i += 2
            else:
                key.append("T")
        elif c == "G":
            if nxt == "H":
                if not (i > 0 and w[i - 1] in _VOWELS):
                    pass                   # silent GH
                else:
                    key.append("K")
                i += 1
            elif nxt == "N":
                pass                       # silent G in GN
            elif nxt in ("I", "E", "Y"):
                key.append("J")
            else:
                key.append("K")
        elif c == "H":
            # H is sounded only between a vowel and a non-vowel-following spot.
            if prev in _VOWELS and nxt not in _VOWELS:
                pass
            elif prev in ("C", "S", "P", "T", "G"):
                pass                       # part of a digraph, handled above
            else:
                key.append("H")
        elif c == "J":
            key.append("J")
        elif c == "K":
            if prev != "C":                # CK -> single K
                key.append("K")
        elif c == "L":
            key.append("L")
        elif c == "M":
            key.append("M")
        elif c == "N":
            key.append("N")
        elif c == "P":
            key.append("F" if nxt == "H" else "P")
            if nxt == "H":
                i += 1
        elif c == "Q":
            key.append("K")
        elif c == "R":
            key.append("R")
        elif c == "S":
            if nxt == "H":
                key.append("X")
                i += 1
            elif nxt == "I" and nxt2 in ("O", "A"):
                key.append("X")            # -SIO-, -SIA-
            else:
                key.append("S")
        elif c == "T":
            if nxt == "H":
                key.append("0")            # TH -> theta
                i += 1
            elif nxt == "I" and nxt2 in ("O", "A"):
                key.append("X")            # -TIO-, -TIA-
            else:
                key.append("T")
        elif c == "V":
            key.append("F")
        elif c == "W" or c == "Y":
            if nxt in _VOWELS:             # sounded only before a vowel
                key.append(c)
        elif c == "X":
            key.append("K")
            key.append("S")
        elif c == "Z":
            key.append("S")

        i += 1

    return "".join(key)


def phonetic_key(text: str) -> str:
    """Concatenated Metaphone of every alphabetic run in `text`.

    Digits and punctuation are dropped, so "node2vec" and "node vec" key the
    same. "" when there is nothing alphabetic to key.
    """
    return "".join(metaphone(m.group(0)) for m in _ALPHA_RE.finditer(text or ""))


def phonetic_similar(a: str, b: str, threshold: float = 0.7) -> bool:
    """True when `a` and `b` plausibly SOUND alike — same mishearing, not a
    rewrite. Compares their phonetic keys by character-similarity ratio.

    Empty keys (no letters on a side) never match: we can't judge a sound we
    don't have, and learning from it would be a coin flip.
    """
    ka, kb = phonetic_key(a), phonetic_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    return SequenceMatcher(None, ka, kb).ratio() >= threshold
