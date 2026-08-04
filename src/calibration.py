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

Two things make comparing a target to a transcript harder than a string diff,
and both are handled here rather than in the shared learners:

**A read-aloud target is written, not spoken.** It carries quotes, commas and
spelled-out numbers that no speaker pronounces. Diffing them verbatim scores
"March 4th at 9.30" as a 30-point miss on a *perfect* recognition, and teaches
the miner to write numerals as words forever after. So both sides are
canonicalized (:mod:`numwords`) before scoring, and depunctuated before learning.

**Whisper capitalizes.** Its raw output is often Title Case For Every Word, and
difflib compares tokens case-sensitively — so a whole sentence collapses into
one giant ``replace`` opcode, which ``_diff_ngram_pairs`` then rejects for being
too wide. The net effect was that most rows taught nothing at all. Casing is
aligned to the target first, which only ever changes the case of words the
target already contains.
"""
from __future__ import annotations

import difflib
import re
import threading
from dataclasses import dataclass, field

from . import asr_artifacts, numwords


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

# Which words in each sentence are dictionary material — stated, not guessed.
#
# The heuristic in `vocab_suggest` has to infer "is this a name?" from a lone
# token, and a leading capital is its main signal. That works mid-sentence but
# not at the start of one, where EVERY word is capitalized: it read "Turn" in
# the last sentence as a proper noun and pinned it, which then biases the
# Whisper decoder prompt AND permanently protects "Turn" from the Title-Case
# flattener. It also has a 3-character floor, so it threw away "Q3" — the worst
# mishearing in a typical run.
#
# Since these eight sentences are fixed and curated, guessing is unnecessary.
# `misheard_terms` falls back to the (hardened) heuristic for any other target.
CALIBRATION_TERMS: dict[str, tuple[str, ...]] = {
    "The quick brown fox jumps over the lazy dog.": (),
    "We deployed the FastAPI service to Kubernetes on Tuesday.": ("FastAPI", "Kubernetes"),
    "Please schedule the meeting for March fourth at nine thirty.": (),
    "The node2vec embeddings improved recall by twelve percent.": ("node2vec",),
    "She said, \"Let's refactor the authentication module first.\"": (),
    "Our Q3 revenue grew to eighteen point five million dollars.": ("Q3",),
    "Johnson reviewed the PostgreSQL migration and approved it.": ("Johnson", "PostgreSQL"),
    "Turn the volume up and open the settings folder.": (),
}

_EDGE_PUNCT = re.compile(r"^[^\w]+|[^\w]+$")
# A capital carries no name-evidence right after these.
_OPENERS = "\"'“‘([{"


def _words(s: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", (s or "").lower())


def _core(tok: str) -> str:
    return _EDGE_PUNCT.sub("", tok or "")


def word_accuracy(target: str, heard: str) -> float:
    """Token-level agreement in [0,1] — a baseline-accuracy proxy.

    Matched tokens over the longer side, so both *missing* and *extra* words
    pull it down. 1.0 = exact, 0.0 = nothing in common.

    Numbers are canonicalized on both sides first, so "nine thirty" and "9.30"
    agree: that difference is notation, not misrecognition, and charging for it
    made the headline baseline read far worse than the microphone deserved.
    """
    t, h = numwords.normalize(target), numwords.normalize(heard)
    if not t and not h:
        return 1.0
    if not t or not h:
        return 0.0
    matches = sum(b.size for b in
                  difflib.SequenceMatcher(a=t, b=h, autojunk=False).get_matching_blocks())
    return matches / max(len(t), len(h))


def _collapse_false_start(target: str, heard: str) -> str:
    """Drop a restart: "The quick fox. The quick brown fox…" → the clean read.

    Only fires when the tail of `heard` matches the target *exactly* (after
    number canonicalization), which makes it provably a re-read rather than a
    guess. A stumble is not a recognition error, and leaving it in both depresses
    the score and feeds the miner a phantom substitution.
    """
    h_toks = (heard or "").split()
    t_norm = numwords.normalize(target)
    if not t_norm or len(h_toks) <= len(t_norm):
        return heard
    tail = h_toks[-len(t_norm):]
    if numwords.normalize(" ".join(tail)) == t_norm:
        return " ".join(tail)
    return heard


def _depunctuate(s: str) -> list[str]:
    """Whitespace tokens with edge punctuation removed.

    Interior characters survive, so "9.30", "18.5", "node2vec" and "Let's" are
    untouched — only the quotes, commas and full stops a speaker cannot
    pronounce are dropped.
    """
    return [c for c in (_core(tok) for tok in (s or "").split()) if c]


def _align_casing(target_toks: list[str], heard_toks: list[str]) -> list[str]:
    """Give shared words the target's casing.

    Can only change case, and only for words the target already contains, so it
    cannot invent or alter content. Where the target uses a word both at the
    start of a sentence and inside one ("The"/"the"), the lowercase form wins —
    otherwise the sentence-initial capital spreads to every later occurrence.
    """
    canon: dict[str, str] = {}
    for core in target_toks:
        lc = core.lower()
        prev = canon.get(lc)
        if prev is None or (prev[:1].isupper() and not core[:1].isupper()):
            canon[lc] = core
    return [canon.get(core.lower(), core) for core in heard_toks]


def _is_plain_title(word: str) -> bool:
    """True for an ordinary Capitalized word — not SQL, API, TikTok or node2vec."""
    if not word or not word[:1].isupper() or any(c.isdigit() for c in word):
        return False
    rest = word[1:]
    return bool(rest) and rest == rest.lower() and any(c.isalpha() for c in rest)


def _readable_casing(target: str, heard: str) -> str:
    """Show `heard` the way a finished dictation would actually look.

    Whisper Capitalizes Every Word when its decoder is primed with a prompt of
    proper nouns. On the real dictation path `Cleaner._finalize` flattens that
    back down, but calibration reads the RAW transcript on purpose — so the
    storm reached the screen, and the results table looked like Echo Flow would
    hand you Title Case. It won't.

    Casing is not what this page measures (`word_accuracy` lowercases both
    sides), so displaying the storm only misinforms. Words the target contains
    take the target's casing; other ordinary capitalized words are lowercased;
    acronyms, internal-caps brands and digit-bearing tokens are left alone. The
    untouched transcript stays available as `heard_raw`.
    """
    canon: dict[str, str] = {}
    for core in _depunctuate(target):
        lc = core.lower()
        prev = canon.get(lc)
        if prev is None or (prev[:1].isupper() and not core[:1].isupper()):
            canon[lc] = core

    raw_tokens = (heard or "").split()
    out: list[str] = []
    for tok, is_initial in zip(raw_tokens, _sentence_initial_flags(raw_tokens)):
        core = _core(tok)
        if not core:
            out.append(tok)
            continue
        fixed = canon.get(core.lower())
        if fixed is None:
            fixed = core.lower() if _is_plain_title(core) else core
        if is_initial and fixed[:1].islower():
            fixed = fixed[:1].upper() + fixed[1:]
        out.append(tok.replace(core, fixed, 1))
    return " ".join(out)


def _learning_pair(target: str, heard: str) -> tuple[str, str]:
    """The (heard, target) strings to hand the pattern miner.

    Strips Whisper's trailing artifacts, collapses a false start, drops
    unpronounceable punctuation from both sides, and aligns casing — leaving
    only the differences that are actually mishearings.
    """
    heard = asr_artifacts.strip_trailing(heard)
    heard = _collapse_false_start(target, heard)
    t_toks = _depunctuate(target)
    h_toks = _align_casing(t_toks, _depunctuate(heard))
    return " ".join(h_toks), " ".join(t_toks)


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
        """[{target, heard, heard_raw, accuracy}] for every recorded sentence.

        `heard` is the transcript with Whisper's own artifacts removed and its
        Title Case flattened the way the cleanup layer would, so the text shown
        to the user matches both the score beside it and what a real dictation
        produces; `heard_raw` keeps the untouched transcript.
        """
        out: list[dict] = []
        with self._lock:
            snapshot = list(zip(self.sentences, self.heard))
        for tgt, hrd in snapshot:
            if hrd:
                clean = _collapse_false_start(tgt, asr_artifacts.strip_trailing(hrd))
                clean = _readable_casing(tgt, clean)
                out.append({"target": tgt, "heard": clean, "heard_raw": hrd,
                            "accuracy": word_accuracy(tgt, clean)})
        return out

    def baseline_accuracy(self) -> float | None:
        ps = self.pairs()
        return sum(p["accuracy"] for p in ps) / len(ps) if ps else None


def _sentence_initial_flags(tokens: list[str]) -> list[bool]:
    """Which raw tokens sit where a capital letter proves nothing."""
    flags: list[bool] = []
    prev_ended = True
    for tok in tokens:
        flags.append(prev_ended or tok[:1] in _OPENERS)
        prev_ended = tok.rstrip("\"'“”’)]}").endswith((".", "!", "?"))
    return flags


def misheard_terms(target: str, heard: str) -> list[str]:
    """Dictionary-worthy words in `target` that did NOT survive into `heard`.

    These are the names/technical tokens the decoder should be biased toward —
    known ground truth, so they can be pinned directly rather than merely
    suggested.

    For the shipped sentences the candidates are stated in `CALIBRATION_TERMS`
    rather than inferred. For any other target (the unit tests, and any future
    personalized sentence set) it falls back to the shared `_looks_like_term`
    heuristic, hardened with what a full sentence tells us that a lone token
    cannot: a capital at the start of a sentence — or right after an opening
    quote — is grammar, not a name.
    """
    from .vocab_suggest import _looks_like_term
    heard_lc = set(_words(heard))

    curated = CALIBRATION_TERMS.get(target)
    if curated is not None:
        return [t for t in curated if t.lower() not in heard_lc]

    raw_tokens = re.findall(r"[^\s]+", target or "")
    initial = _sentence_initial_flags(raw_tokens)
    out: list[str] = []
    seen: set[str] = set()
    for tok, is_initial in zip(raw_tokens, initial):
        core = _core(tok)
        lc = core.lower()
        if not core or lc in seen or lc in heard_lc:
            continue
        # min_len=2 because ground truth knows Q3 / S3 / AI really are terms.
        if _looks_like_term(core, allow_leading_cap=not is_initial, min_len=2):
            seen.add(lc)
            out.append(core)
    return out


def apply_seeds(session: CalibrationSession, pattern_miner, dict_conn) -> dict:
    """Seed the learners from a completed session. Returns a counts summary.

    - Every (heard → target) pair is recorded into the pattern miner, so the
      substitutions Whisper needs are learned (and reinforced across runs).
      Pairs are weighted 2, because `confident_patterns` will not apply anything
      below `total >= 2` — at weight 1 a calibration run stored corrections that
      sat inert until the same error happened again by chance, which is exactly
      the wait calibration exists to skip.
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
        if pattern_miner is not None and heard:
            learn_heard, learn_target = _learning_pair(target, heard)
            if learn_heard and learn_heard != learn_target:
                try:
                    recorded += pattern_miner.record(
                        learn_heard, learn_target,
                        drop_number_format=True, weight=2,
                    )
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
