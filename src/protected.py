"""Hard-exclude zones for the humanizer — spans the model must never touch.

In a methods section, the numbers, hyperparameters, splits, metrics, citations,
quotes and code ARE the content; precision there reads "competent" to a
reviewer, and a humanizer that "improves the flow" of "F1 of 0.79" into "an F1
of about 0.8" is doing damage. So instead of trusting the model to be careful,
we make it impossible for it to be careless.

:func:`find` locates every protected span. ``Cleaner.humanize_text`` uses it for
*structural* protection: any SENTENCE that carries a span is held byte-for-byte
and never sent to the model — only the free-prose runs around it are rewritten.
This is deliberate. No local model reliably preserves an in-line placeholder
token: both the small dictation model and the escalation model paraphrase a
masked ``⟦0⟧`` into "zero", so span-level masking silently corrupts the very
facts it was meant to guard. Keeping the whole sentence is coarser but honest —
the tool refuses to edit the facts rather than trust the model with them.

The placeholder helpers below (:func:`mask` / :func:`intact` / :func:`unmask`)
remain for a future cloud path, where a frontier model *does* preserve the
tokens and finer-grained, span-level rewriting becomes safe.

Pure, dependency-free.
"""
from __future__ import annotations

import re

# ⟦N⟧ — mathematical brackets, effectively never seen in prose, and small models
# pass them through. The restore/verify regex tolerates stray spaces the model
# may insert (⟦ 0 ⟧); anything worse trips the intact() check and the rewrite is
# refused.
_PH = "⟦{}⟧"
_PH_RE = re.compile(r"⟦\s*(\d+)\s*⟧")

# Ordered most-specific / most-enclosing first, so a citation inside a quote is
# claimed by the quote, not double-masked.
_PATTERNS: list[re.Pattern] = [
    re.compile(r"```.*?```", re.S),                          # fenced code
    re.compile(r"`[^`\n]+`"),                                # inline code
    re.compile(r'"[^"\n]{0,300}"'),                          # "quoted"
    re.compile(r"[“][^”\n]{0,300}[”]"),       # “curly quoted”
    re.compile(r"https?://\S+|www\.\S+", re.I),              # URLs
    re.compile(r"\b10\.\d{4,}/\S+"),                         # DOI
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),             # email
    re.compile(r"\[\d+(?:\s*[-,–]\s*\d+)*\]"),               # [1], [3-5], [2, 7]
    re.compile(                                              # (Smith et al., 2020)
        r"\([A-Z][A-Za-z.'’-]+(?:\s+et\s+al\.?)?"
        r"(?:\s+(?:and|&)\s+[A-Z][A-Za-z.'’-]+)*,?\s+\d{4}[a-z]?\)"),
    re.compile(r"\b[A-Za-z_]\w*\s*=\s*-?\d[\d.eE+-]*\w*"),   # lr=0.001, batch=32
    re.compile(                                              # 12.5ms, 3GB, 60fps
        r"\b\d+(?:\.\d+)?\s*(?:%|ms|µs|us|ns|s|min|hr|GB|MB|KB|kB|TB|GHz|MHz|Hz|"
        r"fps|px|pt|dpi|bp|x)\b", re.I),
    re.compile(r"[$€£]\s?\d[\d,]*(?:\.\d+)?[MBK]?", re.I),   # $18.5M
    re.compile(r"\b\d+\s*/\s*\d+\b"),                        # 80/20 split
    re.compile(r"\bv?\d+\.\d+(?:\.\d+)*\b"),                 # 0.79, v1.2.3
    re.compile(r"\b\d{2,}\b"),                               # 14, 2020
]


def find(text: str) -> list[tuple[int, int]]:
    """De-overlapped (start, end) spans to protect, in order of appearance."""
    text = text or ""
    spans: list[tuple[int, int]] = []
    for rx in _PATTERNS:
        for m in rx.finditer(text):
            if m.end() > m.start():
                spans.append((m.start(), m.end()))
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    chosen: list[tuple[int, int]] = []
    last = -1
    for s, e in spans:
        if s >= last:
            chosen.append((s, e))
            last = e
    return chosen


def mask(text: str) -> tuple[str, list[str]]:
    """Return ``(masked_text, originals)`` — each protected span replaced by a
    ⟦N⟧ placeholder; ``originals[N]`` is what it stood for."""
    text = text or ""
    chosen = find(text)
    if not chosen:
        return text, []
    out: list[str] = []
    originals: list[str] = []
    pos = 0
    for i, (s, e) in enumerate(chosen):
        out.append(text[pos:s])
        out.append(_PH.format(i))
        originals.append(text[s:e])
        pos = e
    out.append(text[pos:])
    return "".join(out), originals


def intact(text: str, n: int) -> bool:
    """True only if placeholders 0..n-1 each appear EXACTLY once in ``text`` —
    the model preserved every protected span. (Reordering is tolerated; the span
    content is still exact.)"""
    found = [int(m.group(1)) for m in _PH_RE.finditer(text or "")]
    return sorted(found) == list(range(n))


def unmask(text: str, originals: list[str]) -> str:
    """Restore ⟦N⟧ placeholders to their original spans, byte-for-byte."""
    def _repl(m: re.Match) -> str:
        i = int(m.group(1))
        return originals[i] if 0 <= i < len(originals) else m.group(0)
    return _PH_RE.sub(_repl, text or "")


def count(text: str) -> int:
    return len(find(text))
