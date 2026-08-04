"""Canonical number forms, so notation differences aren't scored as errors.

The calibration target says "March fourth at nine thirty"; Whisper writes
"March 4th at 9.30". Recognition was *perfect* — only the notation differs. Two
things went wrong with that before this module existed:

  1. the accuracy metric charged it a 30-point miss, making the headline
     baseline read far worse than the microphone actually did, and
  2. the pattern miner learned ``4th -> fourth`` and began rewriting numerals
     into words in every later dictation.

Both are fixed by canonicalizing *both sides* before comparing. That symmetry is
the safety argument: a conversion that is wrong on one side is impossible,
because the same function runs on the other, and a conversion wrong on both
sides is a no-op for equality.

Deliberately token-wise — no multi-word accumulation. "twenty one" stays
``['20','1']`` rather than becoming ``['21']``, because the digit form "21"
would have to be split to match anyway, and the paired-off comparison is what
matters, not the arithmetic.

Known limits (none occur in CALIBRATION_SENTENCES, all fail *safely* — they
compare unequal rather than compare wrongly):
  - "one hundred" -> ['1','100'] but "100" -> ['100']
  - "twenty-first" -> ['20','1'] but "21st" -> ['21']
  - "nine oh five" -> ['9','oh','5'] but "9:05" -> ['9','5']

Pure stdlib.
"""
from __future__ import annotations

import re

_CARDINALS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90",
}

_ORDINALS = {
    "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5",
    "sixth": "6", "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10",
    "eleventh": "11", "twelfth": "12", "thirteenth": "13", "fourteenth": "14",
    "fifteenth": "15", "sixteenth": "16", "seventeenth": "17",
    "eighteenth": "18", "nineteenth": "19", "twentieth": "20",
    "thirtieth": "30", "fortieth": "40", "fiftieth": "50", "sixtieth": "60",
    "seventieth": "70", "eightieth": "80", "ninetieth": "90",
}

# "point" in "eighteen point five" is the spoken decimal separator; the digit
# form writes it as "." which the tokenizer has already dropped.
_DECIMAL_WORDS = {"point", "dot"}

# Scale words are NOT converted: "18.5 million" and "eighteen point five
# million" both keep "million" verbatim, so they already agree.

_WORDS = re.compile(r"[A-Za-z0-9']+")
_DIGIT_ORDINAL = re.compile(r"^(\d+)(?:st|nd|rd|th)$")


def _canonical_token(tok: str) -> str:
    """One token to its canonical form. Non-numeric tokens pass through."""
    if tok in _CARDINALS:
        return _CARDINALS[tok]
    if tok in _ORDINALS:
        return _ORDINALS[tok]
    m = _DIGIT_ORDINAL.match(tok)
    if m:
        return str(int(m.group(1)))
    if tok.isdigit():
        return str(int(tok))        # "05" and "5" are the same number
    return tok


def normalize_tokens(tokens: list[str]) -> list[str]:
    """Canonicalize an already-lowercased token list."""
    out = [_canonical_token(t) for t in tokens]
    # Second pass: drop the spoken decimal separator, but only where it really
    # separates two numbers — "point" is an ordinary noun elsewhere ("the point
    # of the meeting"), and dropping it there would hide a real mishearing.
    result: list[str] = []
    for i, tok in enumerate(out):
        if (tok in _DECIMAL_WORDS and result and result[-1].isdigit()
                and i + 1 < len(out) and out[i + 1].isdigit()):
            continue
        result.append(tok)
    return result


def normalize(text: str) -> list[str]:
    """Tokenize `text` and canonicalize its numbers.

    "%" becomes the word "percent" first, so "12%" and "twelve percent" agree.
    """
    lowered = (text or "").lower().replace("%", " percent ")
    return normalize_tokens(_WORDS.findall(lowered))


def same_number(a: str, b: str) -> bool:
    """True when `a` and `b` are the same number written two ways.

    Used to keep the pattern miner from learning notation as a correction:
    ``same_number("4th", "fourth")`` is True, so that pair is dropped, while
    ``same_number("From", "Turn")`` is False and the genuine mishearing is kept.
    Requires an actual digit in the result, so two equal non-numeric strings
    don't qualify by accident.
    """
    na, nb = normalize(a), normalize(b)
    if na != nb:
        return False
    return any(t.isdigit() for t in na)
