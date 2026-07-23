"""Guided voice calibration — read known sentences, learn the mishearings.

Instead of waiting for accent errors to surface organically, calibration gets
*ground truth*: the user reads a fixed set of sentences aloud, and comparing
what Whisper HEARD to the known TARGET yields (heard → target) correction pairs
that seed the Phase-1 learners deterministically, plus a baseline accuracy.

It reuses the real dictation pipeline. The daemon checks for an active session
in ``_do_dictation``: while one is live, each spoken utterance is matched to the
current target sentence instead of being cleaned and pasted. The session lives
in memory on the ``App`` (the dashboard shares the same object in-process), so
no IPC is involved — just a small lock, since the dictation thread writes and
the dashboard threads read.

The session logic here is pure/stdlib and testable; the DB writes (seeding the
dictionary + pattern miner) live in :func:`apply_seeds`, which takes the miner
and dictionary connection so it can be tested against a temp database.
"""
from __future__ import annotations

import difflib
import re
import threading
from dataclasses import dataclass, field


# Curated for phonetic spread AND the kinds of tokens Echo Flow users actually
# dictate — product/tech names, numbers, proper nouns, a quote — because those
# are what Whisper mishears, and the point is to catch YOUR systematic errors.
CALIBRATION_SENTENCES = [
    "The quick brown fox jumps over the lazy dog.",
    "We deployed the FastAPI service to Kubernetes on Tuesday.",
    "Please schedule the meeting for March fourth at nine thirty.",
    "The node2vec embeddings improved recall by twelve percent.",
    "She said, \"Let's refactor the authentication module first.\"",
    "Our Q3 revenue grew to eighteen point five million dollars.",
    "Johnson reviewed the PostgreSQL migration and approved it.",
    "Turn the volume up and open the settings folder.",
]


def _words(s: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", (s or "").lower())


def word_accuracy(target: str, heard: str) -> float:
    """Token-level agreement in [0,1] — a baseline-accuracy proxy.

    Matched tokens over the longer side, so both *missing* and *extra* words
    pull it down. 1.0 = exact, 0.0 = nothing in common.
    """
    t, h = _words(target), _words(heard)
    if not t and not h:
        return 1.0
    if not t or not h:
        return 0.0
    matches = sum(b.size for b in
                  difflib.SequenceMatcher(a=t, b=h, autojunk=False).get_matching_blocks())
    return matches / max(len(t), len(h))


@dataclass
class CalibrationSession:
    """A read-aloud run: the target sentences and what Whisper heard for each."""
    sentences: list[str]
    heard: list[str] = field(default_factory=list)   # index-aligned to sentences
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self):
        if not self.heard:
            self.heard = [""] * len(self.sentences)

    @property
    def index(self) -> int:
        """Index of the next unread sentence (== number recorded so far)."""
        with self._lock:
            for i, h in enumerate(self.heard):
                if not h:
                    return i
            return len(self.sentences)

    @property
    def active(self) -> bool:
        return self.index < len(self.sentences)

    @property
    def done(self) -> bool:
        return not self.active

    def submit(self, transcript: str) -> int:
        """Record `transcript` for the current sentence. Returns the 1-based
        index just recorded, or -1 if the session was already complete."""
        with self._lock:
            for i, h in enumerate(self.heard):
                if not h:
                    self.heard[i] = (transcript or "").strip()
                    return i + 1
            return -1

    def progress(self) -> dict:
        idx = self.index
        return {
            "total": len(self.sentences),
            "recorded": idx,
            "done": idx >= len(self.sentences),
            "current": self.sentences[idx] if idx < len(self.sentences) else None,
        }

    def pairs(self) -> list[dict]:
        """[{target, heard, accuracy}] for every recorded sentence."""
        out: list[dict] = []
        with self._lock:
            snapshot = list(zip(self.sentences, self.heard))
        for tgt, hrd in snapshot:
            if hrd:
                out.append({"target": tgt, "heard": hrd,
                            "accuracy": word_accuracy(tgt, hrd)})
        return out

    def baseline_accuracy(self) -> float | None:
        ps = self.pairs()
        return sum(p["accuracy"] for p in ps) / len(ps) if ps else None


def misheard_terms(target: str, heard: str) -> list[str]:
    """Dictionary-worthy words in `target` that did NOT survive into `heard`.

    These are the names/technical tokens the decoder should be biased toward —
    known ground truth, so they can be pinned directly rather than merely
    suggested. Uses the same "looks like a term" test as the suggestion path.
    """
    from .vocab_suggest import _looks_like_term
    heard_lc = {w for w in _words(heard)}
    out: list[str] = []
    seen: set[str] = set()
    for tok in re.findall(r"[^\s]+", target or ""):
        core = re.sub(r"^[^\w]+|[^\w]+$", "", tok)
        lc = core.lower()
        if not core or lc in seen or lc in heard_lc:
            continue
        if _looks_like_term(core):
            seen.add(lc)
            out.append(core)
    return out


def apply_seeds(session: CalibrationSession, pattern_miner, dict_conn) -> dict:
    """Seed the learners from a completed session. Returns a counts summary.

    - Every (heard → target) pair is recorded into the pattern miner, so the
      substitutions Whisper needs are learned (and reinforced across runs).
    - Target terms (names / technical tokens) that were misheard are pinned
      DIRECTLY into the dictionary — we have ground truth, so there is nothing to
      merely "suggest". Idempotent via `vocabulary.add_term`.
    """
    from .dashboard import vocabulary
    pairs = session.pairs()
    recorded = pinned = 0
    pinned_terms: list[str] = []
    for p in pairs:
        target, heard = p["target"], p["heard"]
        if pattern_miner is not None and heard and heard != target:
            try:
                recorded += pattern_miner.record(heard, target)
            except Exception:
                pass
        if dict_conn is not None:
            for term in misheard_terms(target, heard):
                try:
                    vocabulary.add_term(dict_conn, term)
                    pinned += 1
                    pinned_terms.append(term)
                except Exception:
                    pass
    return {"pairs": len(pairs), "recorded": recorded,
            "pinned": pinned, "pinned_terms": pinned_terms}
