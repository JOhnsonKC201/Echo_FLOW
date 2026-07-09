"""Phase 14+ — the local intent model: a regex-miss fallback for Action Mode.

Action Mode (`src/voice_actions.py`) classifies a prefixed utterance with a set
of tight, anchored regexes. That is high-precision but brittle to phrasing: a
user who says *"launch spotify"* instead of *"open spotify"*, or *"play some
music"* instead of *"play music"*, falls straight through to "unknown command".

This module is the opt-in recovery layer for exactly those misses. It is built
around one locked safety invariant:

    The intent model NEVER fires a side effect the regex/allowlist wouldn't.
    It predicts a handler NAME + a slot STRING only. That slot is re-parsed and
    re-validated by :func:`build_match`, which reuses the SAME deterministic
    guards as ``voice_actions.classify`` (``_domain_to_url``/``_is_safe_url``/
    ``user_targets``) — and is *stricter* for allowlisted handlers (it refuses a
    non-configured app/folder at construction time). So whatever proposes the
    action — a keyword heuristic today, an embedding model tomorrow — it can
    only ever resolve to an already-safe ``ActionMatch``.

Because of that spine, the *predictor* is deliberately swappable. Today it is a
dependency-free :class:`KeywordPredictor` (verb-synonym rules, no ML deps, no
import cost). A future embedding + logistic-regression head can be dropped in
via :func:`set_predictor` without touching a single guard.

The feature is OFF by default (``experimental.action_intent_model: false``) and
supports a ``"shadow"`` mode that logs what it *would* have done without
executing — so precision can be measured before the model is ever trusted to
fire. See ``scripts/eval_intent.py`` for the offline precision/recall harness.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import voice_actions as _va


# Latency + false-positive pre-gate: commands are short. A long utterance is
# almost always dictation, not a mis-phrased command, so we abstain cheaply
# before any predictor work. (Roadmap MODEL-LATENCY.)
MAX_BODY_CHARS = 80
MAX_BODY_WORDS = 12

# Default confidence floors. The keyword predictor emits hardcoded ~0.8–0.9
# confidences, so it wants a high floor. The embedding model emits a diffuse
# 13-class softmax (correct predictions cluster ~0.5), so it wants a much lower
# floor — its `none` class does most of the abstaining; the floor is a secondary
# guard. Empirically ~0.4 (see `scripts/train_intent.py --eval`).
DEFAULT_MIN_CONF = 0.75
DEFAULT_MODEL_MIN_CONF = 0.4


@dataclass(frozen=True)
class Prediction:
    """A predictor's raw guess: a handler id + the slot text it extracted.

    ``handler == "none"`` is the explicit abstain — the predictor saw nothing
    it recognized as an action.
    """
    handler: str
    slot: str
    confidence: float


@dataclass(frozen=True)
class InferenceResult:
    """The full outcome of one inference, carrying enough for both the live
    path (use ``match``) and shadow logging (inspect ``prediction``/``gated``).
    """
    prediction: Prediction
    gated: bool                 # did confidence clear the floor?
    match: "_va.ActionMatch | None"  # built ONLY if gated AND re-validation passed


_NONE = Prediction("none", "", 0.0)


# --- The safety spine: re-validate a predicted (handler, slot) ---------------

_MEDIA_KEYS = {
    "playpause": "Play / pause",
    "nexttrack": "Next track",
    "prevtrack": "Previous track",
    "volumemute": "Mute",
}


def build_match(handler: str, slot: str, cfg: dict,
                history=None) -> "_va.ActionMatch | None":
    """Re-validate a predicted (handler, slot) through the SAME guards as
    ``voice_actions.classify``, returning a safe ``ActionMatch`` or ``None``.

    This is the sole authority on whether a prediction may execute. It is
    intentionally *stricter* than ``classify`` for allowlisted handlers:
    ``classify`` returns an ``open_app`` match even for an unknown app (so
    dispatch can explain "no such app"), but a *model* proposing an app must
    resolve to a configured one here — an unrecognized name is simply refused,
    never surfaced as a toast. Never raises.
    """
    slot = (slot or "").strip()

    if handler == "open":
        # url-or-app, exactly mirroring classify()'s _RE_OPEN catch-all: a
        # spoken object that parses as a domain opens as a URL, otherwise it is
        # an app-name lookup (refused unless configured).
        url = _va._domain_to_url(slot)
        if url:
            return _va.ActionMatch("open_url", f"Open {url}", {"url": url})
        app = slot.lower()
        if app and app in _va.user_targets("app", cfg, history):
            return _va.ActionMatch("open_app", f"Open {slot}", {"app": app})
        return None

    if handler == "open_url":
        url = _va._domain_to_url(slot)
        return _va.ActionMatch("open_url", f"Open {url}", {"url": url}) if url else None

    if handler == "open_app":
        app = slot.lower()
        if app and app in _va.user_targets("app", cfg, history):
            return _va.ActionMatch("open_app", f"Open {slot}", {"app": app})
        return None

    if handler == "open_folder":
        folder = slot.lower()
        if folder and folder in _va.user_targets("folder", cfg, history):
            return _va.ActionMatch("open_folder", f"Open {slot} folder",
                                   {"folder": folder})
        return None

    if handler == "web_search":
        return _va.ActionMatch("web_search", f"Search the web for “{slot}”",
                               {"query": slot}) if slot else None

    if handler == "quick_note":
        return _va.ActionMatch("quick_note", "Take a note",
                               {"body": slot}) if slot else None

    if handler == "draft_event":
        return _va.ActionMatch("draft_event", f"Draft event: {slot[:40]}",
                               {"details": slot}) if slot else None

    if handler == "summarize_focused":
        return _va.ActionMatch("summarize_focused",
                               "Summarize the focused document", {})

    if handler == "open_clipboard_link":
        return _va.ActionMatch("open_clipboard_link", "Open clipboard link", {})

    if handler == "media_key":
        label = _MEDIA_KEYS.get(slot)
        return _va.ActionMatch("media_key", label, {"key": slot}) if label else None

    if handler == "volume":
        if slot in ("up", "down"):
            return _va.ActionMatch("volume", f"Volume {slot}", {"dir": slot})
        return None

    return None   # unknown handler id — refuse.


# --- The predictor (swappable; keyword heuristic today) ----------------------

# A leading politeness / filler run we strip before looking for the verb, so
# "can you please open spotify" reduces to "open spotify". Order-independent and
# repeated (matched greedily as a run).
_FILLER = re.compile(
    r"^(?:(?:hey|ok|okay|um|uh|so|well|please|kindly|now|just|"
    r"could you|can you|would you|will you|would you please|"
    r"i want to|i'd like to|i would like to|i wanna|"
    r"let's|lets|go ahead and|for me)[\s,]+)+",
    re.I,
)
_TRAILING = re.compile(r"[\s,]+(?:please|thanks|thank you|thank u|for me)\s*$", re.I)


def normalize_command(body: str) -> str:
    """Shared command-text normalization for every predictor: strip surrounding
    whitespace + trailing sentence punctuation (an STT/cleanup period is common),
    a leading politeness/filler run, and a trailing politeness word. Mirrors
    voice_actions.classify()'s own trimming so the same transcript reaches both
    the regex path and any predictor."""
    b = (body or "").strip().rstrip(" .!?,")
    b = _FILLER.sub("", b)
    return _TRAILING.sub("", b).strip().rstrip(" .!?,")


@dataclass
class _Rule:
    """One heuristic: a pattern over the cleaned body → (handler, slot, conf).

    ``slot_group`` selects the capture group used as the slot (0 = fixed slot
    given by ``fixed_slot``). ``conf`` is the base confidence for a clean lead.
    """
    pattern: "re.Pattern[str]"
    handler: str
    conf: float
    slot_group: int = 1
    fixed_slot: str = ""


def _rules() -> "list[_Rule]":
    R = _Rule
    return [
        # clipboard link — a specific "open … clipboard" form, so it must come
        # BEFORE the generic open/launch rule (mirrors classify()'s ordering of
        # specific "open …" phrases ahead of the _RE_OPEN catch-all).
        R(re.compile(r"^open\s+(?:the\s+|my\s+)?clipboard(?:\s+(?:link|url))?$|^open\s+(?:the\s+)?(?:link|url)\s+(?:in|from)\s+(?:my\s+|the\s+)?clipboard$", re.I),
          "open_clipboard_link", 0.80, slot_group=0),
        # open / launch an app OR a website. The "open" handler re-parses the
        # slot in build_match to decide app-vs-url, so the predictor just hands
        # over the object (an unknown app resolves to nothing → abstains). Bare
        # "open" is here for the filler-prefixed case ("can you please open X"):
        # classify()'s _RE_OPEN only fires when the body STARTS with "open", so a
        # leading-filler "open" reaches the model post-strip and must recover.
        R(re.compile(r"^(?:launch|start(?:\s+up)?|fire\s+up|boot\s+up|load|open(?:\s+up)?)\s+(.+)$", re.I),
          "open", 0.90),
        # go / navigate to a site (URL only — "navigate to spotify" is not an app).
        R(re.compile(r"^(?:go\s+to|goto|navigate\s+to|take\s+me\s+to|visit|browse\s+to|pull\s+up|bring\s+up)\s+(.+)$", re.I),
          "open_url", 0.88),
        # web search synonyms beyond the strict "search the web for X".
        R(re.compile(r"^(?:google\s+|look\s+up\s+|lookup\s+|search\s+(?:for\s+)?|find\s+(?:me\s+)?)(.+?)(?:\s+(?:online|on\s+google|on\s+the\s+web))?$", re.I),
          "web_search", 0.85),
        # note synonyms beyond "take a note that X".
        R(re.compile(r"^(?:jot(?:\s+down)?|note(?:\s+down)?|write\s+down|remember)\s+(?:that\s+|to\s+)?(.+)$", re.I),
          "quick_note", 0.82),
        # "remind me ..." is ambiguous (note vs event); keep it below the
        # default floor so it abstains unless the user lowers min_conf.
        R(re.compile(r"^remind\s+me\s+(?:that\s+|to\s+)?(.+)$", re.I),
          "quick_note", 0.70),
        # summarize the focused doc — broader than the strict classify regex.
        R(re.compile(r"^(?:summari[sz]e|sum\s+up|tl;?dr|give\s+me\s+(?:a\s+)?(?:summary|tldr))\b.*$", re.I),
          "summarize_focused", 0.80, slot_group=0),
        # media: play/pause only when followed by generic music words (never a
        # specific track like "play despacito", which we can't honor).
        R(re.compile(r"^(?:play|pause|resume)(?:\s+(?:some|the|my|it)?\s*(?:music|songs?|media|playback|tunes?|it))?\s*$", re.I),
          "media_key", 0.80, slot_group=0, fixed_slot="playpause"),
        R(re.compile(r"^(?:skip|next)(?:\s+(?:this|one|it|the|song|track))*\s*$", re.I),
          "media_key", 0.80, slot_group=0, fixed_slot="nexttrack"),
        R(re.compile(r"^(?:previous|prev|last|go\s+back\s+a?)(?:\s+(?:song|track|one))*\s*$", re.I),
          "media_key", 0.80, slot_group=0, fixed_slot="prevtrack"),
        R(re.compile(r"^(?:(?:un)?mute(?:\s+(?:it|sound|the\s+sound|volume))?|silence(?:\s+it)?)\s*$", re.I),
          "media_key", 0.82, slot_group=0, fixed_slot="volumemute"),
        # volume synonyms (broad phrasings; direction is the fixed slot).
        R(re.compile(r"^(?:(?:turn|crank|pump|bump)\s+(?:it\s+|the\s+volume\s+|up\s+)?up(?:\s+the\s+volume)?|volume\s+up|raise\s+(?:the\s+)?volume|(?:make\s+it\s+)?louder)\s*$", re.I),
          "volume", 0.82, slot_group=0, fixed_slot="up"),
        R(re.compile(r"^(?:(?:turn|bring)\s+(?:it\s+|the\s+volume\s+|down\s+)?down(?:\s+the\s+volume)?|volume\s+down|lower\s+(?:the\s+)?volume|(?:make\s+it\s+)?(?:quieter|softer))\s*$", re.I),
          "volume", 0.82, slot_group=0, fixed_slot="down"),
    ]


class KeywordPredictor:
    """Dependency-free verb-synonym predictor. Deterministic, sub-millisecond,
    zero import cost. Recovers phrasings the anchored ``classify`` regexes miss
    while emitting only a handler + slot for :func:`build_match` to validate.
    """

    def __init__(self) -> None:
        self._rules = _rules()

    def predict(self, body: str) -> Prediction:
        # Shared normalization (see normalize_command): trailing sentence
        # punctuation ("launch spotify.") and a leading/trailing politeness run
        # must not leak into the slot or defeat an anchored ($) rule.
        b = normalize_command(body)
        if not b:
            return _NONE
        for rule in self._rules:
            m = rule.pattern.match(b)
            if not m:
                continue
            if rule.slot_group == 0:
                slot = rule.fixed_slot
            else:
                slot = (m.group(rule.slot_group) or "").strip()
                # collapse STT-doubled spaces in extracted objects
                slot = re.sub(r"\s+", " ", slot)
                if not slot:
                    continue   # a verb with no object isn't actionable
            return Prediction(rule.handler, slot, rule.conf)
        return _NONE


# --- Module-cached predictor (load-once seam for a future ML head) -----------

_PREDICTOR: "KeywordPredictor | None" = None


def get_predictor() -> KeywordPredictor:
    """Return the process-wide predictor, constructing it once. This is the
    seam where a heavier model would lazy-load exactly once."""
    global _PREDICTOR
    if _PREDICTOR is None:
        _PREDICTOR = KeywordPredictor()
    return _PREDICTOR


def set_predictor(predictor) -> None:
    """Dependency-inject a predictor (tests, or a future embedding head)."""
    global _PREDICTOR
    _PREDICTOR = predictor


def _predictor_for_cfg(cfg: dict):
    """Choose the predictor backend from config: the keyword heuristic (default)
    or the learned embedding model. A model-backend failure (missing deps, no
    artifact, import error) falls back to the keyword predictor rather than
    breaking the fallback entirely."""
    exp = (cfg.get("experimental", {}) or {})
    backend = str(exp.get("action_intent_backend", "keyword")).strip().lower()
    if backend == "model":
        try:
            from . import intent_classifier as _ic
            return _ic.get_model_predictor(exp.get("action_intent_model_path"))
        except Exception:   # noqa: BLE001 — degrade to keyword, never crash
            return get_predictor()
    return get_predictor()


# --- Public inference API ----------------------------------------------------

def infer(body: str, cfg: dict, history=None, *,
          min_conf: float = DEFAULT_MIN_CONF, predictor=None) -> InferenceResult:
    """Run one inference. Never raises.

    Applies the length pre-gate, asks the predictor for a (handler, slot, conf),
    gates on ``min_conf``, and — only if gated — re-validates through
    :func:`build_match`. The returned :class:`InferenceResult` carries the raw
    prediction (for shadow logging) and the built ``match`` (``None`` unless the
    prediction cleared the floor AND survived re-validation).
    """
    body = (body or "").strip()
    if not body or len(body) > MAX_BODY_CHARS or len(body.split()) > MAX_BODY_WORDS:
        return InferenceResult(_NONE, False, None)

    p = predictor or _predictor_for_cfg(cfg)
    try:
        pred = p.predict(body) or _NONE
    except Exception:   # noqa: BLE001 — a predictor must never break dictation
        return InferenceResult(_NONE, False, None)

    if pred.handler == "none":
        return InferenceResult(pred, False, None)

    try:
        floor = float(min_conf)
    except (TypeError, ValueError):   # a malformed config value can't crash us
        floor = DEFAULT_MIN_CONF
    gated = pred.confidence >= floor
    match = None
    if gated:
        try:
            match = build_match(pred.handler, pred.slot, cfg, history)
        except Exception:   # noqa: BLE001 — defense in depth
            match = None
    return InferenceResult(pred, gated, match)


def classify_with_model(body: str, cfg: dict, history=None, *,
                        min_conf: float = DEFAULT_MIN_CONF,
                        predictor=None) -> "_va.ActionMatch | None":
    """Thin wrapper for the live path: the safe ``ActionMatch`` a gated,
    re-validated prediction resolves to, or ``None`` (→ today's regex-only
    behavior). Mirrors the shape of ``voice_actions.classify``."""
    return infer(body, cfg, history, min_conf=min_conf, predictor=predictor).match
