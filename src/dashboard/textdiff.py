"""Word-level diff for showing what the humanizer actually changed.

A rewrite you can't inspect is a rewrite you have to trust blindly, which is the
wrong posture for a tool that edits your words. This renders the change set so
the user can check it at a glance before copying.

Deliberately tiny and dependency-free (stdlib ``difflib``): it returns opcode
tuples and leaves all escaping and styling to the template, so nothing here can
emit markup.
"""
from __future__ import annotations

import difflib
import re

# Split into words while KEEPING the whitespace and punctuation runs as their
# own tokens, so rejoining the pieces reproduces the original text exactly.
_TOKEN_RE = re.compile(r"\s+|\w+|[^\w\s]")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text or "")


def word_diff(before: str, after: str) -> list[tuple[str, str]]:
    """Return ``[(op, text), …]`` where op is ``equal``, ``delete`` or ``insert``.

    Adjacent runs of the same op are merged so the template renders a handful of
    spans rather than one per word. A ``replace`` opcode becomes a delete
    followed by an insert, which reads correctly as strikethrough-then-new-text.
    """
    a, b = _tokens(before), _tokens(after)
    out: list[tuple[str, str]] = []

    def _emit(op: str, text: str) -> None:
        if not text:
            return
        if out and out[-1][0] == op:
            out[-1] = (op, out[-1][1] + text)
        else:
            out.append((op, text))

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            _emit("equal", "".join(a[i1:i2]))
        elif tag == "delete":
            _emit("delete", "".join(a[i1:i2]))
        elif tag == "insert":
            _emit("insert", "".join(b[j1:j2]))
        else:  # replace
            _emit("delete", "".join(a[i1:i2]))
            _emit("insert", "".join(b[j1:j2]))
    return out


def change_ratio(before: str, after: str) -> float:
    """Fraction of the original's words that the rewrite touched, 0.0–1.0.

    Useful as a one-number summary: a near-zero ratio means the pass barely did
    anything, which the user should be able to see without reading a diff.
    """
    a = [t for t in _tokens(before) if t.strip()]
    if not a:
        return 0.0
    b = [t for t in _tokens(after) if t.strip()]
    same = sum(block.size for block in
               difflib.SequenceMatcher(a=a, b=b, autojunk=False)
               .get_matching_blocks())
    return max(0.0, min(1.0, 1.0 - same / len(a)))
