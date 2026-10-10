"""Spoken formatting commands and the "scratch that" backtrack.

Dictation tools users already know let them say "new line", "new paragraph",
"comma" or "question mark" and get the mark instead of the word. Echo Flow had
no such path: the model prompts were never asked for it, and the LLM-free
paths obviously could not do it. This module adds it as rules, so it works
identically with Ollama, with the learned provider, and with no model at all.

It runs BEFORE the model (see `Cleaner.clean`), so the model sees proper
punctuation and real line breaks rather than the words. Like `fillers.strip`
it is timid on purpose, because the failure modes are asymmetric: a spoken
"comma" left as a word is a blemish the user can see and fix; "the trial
period" turned into "the trial." is a word eaten silently. So:

  - Multi-word commands ("new line", "full stop", "question mark") are
    unambiguous enough to convert anywhere they stand as whole words, EXCEPT
    when a determiner precedes them ("a new line", "the question mark").
  - "comma", "colon", "semicolon" are single words but rarely spoken as nouns
    in prose; the determiner guard covers "a comma splice".
  - "period" is the hard one. It is a common noun ("trial period", "a period
    of time") and the most common spoken terminator. Its rule lives in
    `_period_is_command` and is documented there.
  - "scratch that" / "strike that" delete the clause they end: back to the
    previous sentence boundary or line break. If Whisper punctuated the
    command as its own sentence ("Meet at five. Scratch that."), the sentence
    before it is the one that goes. The text is never emptied.
  - Whisper often writes the mark it heard next to the word ("Ship it, period.").
    A converted command absorbs any identical or stray punctuation around it so
    nothing doubles.

`apply` reports the commands it executed, in order, so the dashboard can show
the user what happened instead of silently rewriting them.
"""
from __future__ import annotations

import re

# Words that turn a following command into an ordinary noun phrase:
# "a new line", "the period", "every comma", "no question mark".
_DETERMINERS = frozenset({
    "a", "an", "the", "this", "that", "these", "those", "each", "every",
    "any", "some", "no", "another", "first", "last", "next",
    "my", "your", "his", "her", "its", "our", "their",
})

# Spoken form -> inserted text. Longest forms first so "new paragraph" is not
# matched as "new" + "paragraph" and "exclamation point" beats nothing shorter.
_BREAKS: tuple[tuple[str, str], ...] = (
    ("new paragraph", "\n\n"),
    ("new line", "\n"),
    ("next line", "\n"),
)
_MARKS: tuple[tuple[str, str], ...] = (
    ("exclamation point", "!"),
    ("exclamation mark", "!"),
    ("question mark", "?"),
    ("full stop", "."),
    ("semicolon", ";"),
    ("colon", ":"),
    ("comma", ","),
    ("period", "."),
)
_SCRATCH = ("scratch that", "strike that")

_WORD = r"[A-Za-z]+"
_PUNCT = ".,;:!?"


def _re_for(phrases: tuple[str, ...]) -> re.Pattern[str]:
    alt = "|".join(re.escape(p).replace(r"\ ", r"\s+") for p in phrases)
    # Whole words only; a trailing punctuation run Whisper attached to the
    # command is captured so the replacement can absorb it.
    return re.compile(rf"(?<![\w'])(?P<cmd>{alt})(?![\w'])(?P<tail>[{_PUNCT}]*)",
                      re.IGNORECASE)


_RE_BREAK = _re_for(tuple(p for p, _ in _BREAKS))
_RE_MARK = _re_for(tuple(p for p, _ in _MARKS))
_RE_SCRATCH = _re_for(_SCRATCH)
_RE_BOUNDARY = re.compile(r"[.!?\n]")


def _norm(cmd: str) -> str:
    return re.sub(r"\s+", " ", cmd).strip().lower()


def _prev_word(text: str, start: int) -> str:
    """The word immediately before position `start`, lowercased, or ''.

    Anything but whitespace between that word and the command (a comma, a
    period) means it is not the command's determiner, so '' is returned.
    """
    before = text[:start]
    m = re.search(rf"({_WORD})\s+$", before)
    return m.group(1).lower() if m else ""


def _next_word(text: str, end: int) -> str:
    """The word right after position `end` (skipping whitespace only), or ''."""
    m = re.match(rf"\s*({_WORD})", text[end:])
    return m.group(1) if m else ""


def _period_is_command(prev_word: str, next_word: str, tail: str, at_end: bool) -> bool:
    """Decide whether a spoken "period" is the terminator or the noun.

    Inputs describe the word's surroundings in the raw transcript:
      prev_word  the word before it, lowercased ('' if punctuation intervened)
      next_word  the word after it in its original casing ('' if none)
      tail       punctuation Whisper wrote directly after it ('' or e.g. '.')
      at_end     True when nothing but whitespace follows

    Return True to convert it to ".", False to leave the word alone.

    The test file pins the behaviour this has to satisfy:
      "ship it period"                 -> command (nothing follows)
      "ship it period Then we rest"    -> command (next word capitalized)
      "Ship it, period."               -> command (Whisper heard a stop)
      "the trial period ended"         -> noun    (next word lowercase)
      "a period of two weeks"          -> noun    (determiner before it)
    """
    # TODO(human): implement the rule. Consider which signal should win when
    # they disagree, e.g. a determiner before it but a capitalized word after
    # ("the period Then"), and whether `tail` alone is strong enough evidence.
    raise NotImplementedError


def _is_noun_phrase(text: str, m: re.Match[str]) -> bool:
    return _prev_word(text, m.start()) in _DETERMINERS


def _scratch(text: str, applied: list[str]) -> str:
    """Delete each struck clause. Runs first so struck commands never fire."""
    while True:
        m = _RE_SCRATCH.search(text)
        if not m:
            return text
        cut_to = m.end()
        # Walk back to the previous boundary. If only whitespace separates it
        # from the command, Whisper gave the command its own sentence, so the
        # clause being struck is the one before that boundary.
        start = m.start()
        while True:
            bounds = [b.end() for b in _RE_BOUNDARY.finditer(text, 0, start)]
            cut_from = bounds[-1] if bounds else 0
            if cut_from and not text[cut_from:start].strip():
                start = cut_from - 1
                continue
            break
        rest = text[cut_to:].lstrip()
        if cut_from and text[cut_from - 1] in ".!?" and rest[:1].islower():
            # The surviving words now open a sentence.
            rest = rest[0].upper() + rest[1:]
        if cut_from and text[cut_from - 1] == "\n":
            # Keep the line break; the struck clause lived on the line after it.
            candidate = text[:cut_from] + rest
        else:
            candidate = (text[:cut_from].rstrip() + " " + rest).strip()
        if not candidate.strip():
            # Never empty the dictation. Leave the words and stop looking.
            return text
        applied.append(_norm(m.group("cmd")))
        text = candidate


def _breaks(text: str, applied: list[str]) -> str:
    def _sub(m: re.Match[str]) -> str:
        if _is_noun_phrase(text, m):
            return m.group(0)
        cmd = _norm(m.group("cmd"))
        applied.append(cmd)
        # Whisper fences a command it took for an aside with commas on both
        # sides ("first line, new line, second"). Only then is the comma before
        # it noise; "Dear Sam, new paragraph" keeps its comma.
        fenced = "," in m.group("tail")
        return ("\x02" if fenced else "\x00") + dict(_BREAKS)[cmd] + "\x01"

    out = _RE_BREAK.sub(_sub, text)
    if "\x00" not in out and "\x02" not in out:
        return out
    # Tidy around each break: drop the space before it (and the fencing comma
    # when there was one), keep a real terminator ("First line."), and eat
    # whitespace after it so the next line starts flush.
    out = re.sub(r"[ \t]*,?[ \t]*\x02", "", out)
    out = re.sub(r"[ \t]*\x00", "", out)
    out = re.sub(r"\x01[ \t]*", "", out)
    return out


def _marks(text: str, applied: list[str]) -> str:
    def _sub(m: re.Match[str]) -> str:
        if _is_noun_phrase(text, m):
            return m.group(0)
        cmd = _norm(m.group("cmd"))
        mark = dict(_MARKS)[cmd]
        if cmd == "period":
            rest = text[m.end():]
            if not _period_is_command(_prev_word(text, m.start()),
                                      _next_word(text, m.end()),
                                      m.group("tail"), not rest.strip()):
                return m.group(0)
        applied.append(cmd)
        return "\x00" + mark + "\x01"

    out = _RE_MARK.sub(_sub, text)
    if "\x00" not in out:
        return out
    # The mark attaches to the previous word, replacing any punctuation Whisper
    # already wrote there ("Ship it, period." -> "Ship it.") and the command's
    # own tail. One space follows unless the text ends or a break follows.
    out = re.sub(rf"[ \t]*[{_PUNCT}]*[ \t]*\x00", "", out)
    out = re.sub(r"\x01[ \t]*(?=\S)", " ", out)
    out = out.replace("\x01", "")
    return out


def apply(text: str) -> tuple[str, list[str]]:
    """Convert spoken commands. Returns (text, commands_applied_in_order)."""
    if not text or not text.strip():
        return text, []
    applied: list[str] = []
    out = _scratch(text, applied)
    out = _breaks(out, applied)
    out = _marks(out, applied)
    return out, applied
