"""Phase 14+ — the embedding intent classifier: the ML head behind the spine.

`KeywordPredictor` (in `intent_model.py`) recovers phrasings via hand-written
verb-synonym rules; it is precise but only knows the synonyms it was given. This
module is the *learned* alternative: it embeds the utterance with the repo's
existing sentence-transformers model (`retrieval.embed`, 384-dim, CPU) and runs
a tiny multinomial logistic regression over that vector to predict an intent —
so it generalizes to phrasings no rule anticipated ("crank the tunes" → volume,
"give me the gist" → summarize).

It is still just a *predictor*: it emits a `Prediction(handler, slot, conf)` and
nothing else. The slot is re-parsed and every action is re-validated by
`intent_model.build_match` through the exact same allowlist/URL guards as the
regex path — the model can never fire anything those guards wouldn't allow. It
plugs in via `intent_model` selecting this predictor when
`experimental.action_intent_backend: model`.

Design choices that keep it local-first and dependency-free:
  - Logistic regression, hand-rolled in numpy (no scikit-learn / torch beyond
    the embedder the app already ships). ~13 classes × 384 weights — trivial.
  - Trained out-of-the-box from a shipped seed corpus (`intent_seed.SEED`), so a
    fresh install works with zero user data; `scripts/train_intent.py` can
    retrain with the user's own mined history to sharpen it.
  - Lazy: the embedder and the fitted model load once, on first prediction, and
    the artifact is cached to disk. The length pre-gate in `intent_model.infer`
    runs BEFORE this, so long dictations never reach the embedder.
"""
from __future__ import annotations

import os

import numpy as np

from . import intent_model as _im
from .intent_seed import LABEL_SPEC, SEED

# Bump when the artifact format or seed semantics change so a stale cache on
# disk is ignored rather than mis-loaded.
ARTIFACT_VERSION = 1
DEFAULT_ARTIFACT_PATH = os.path.join("data", "intent_model.npz")


# --- Embedder abstraction (real adapter + injectable fake for tests) ---------

class RepoEmbedder:
    """Adapts the app's `retrieval` embedder to the classifier's needs. Lazy:
    importing/loading sentence-transformers is deferred to first use."""

    def name(self) -> str:
        from . import retrieval
        return retrieval.Retriever.model_name()

    def embed_many(self, texts: "list[str]") -> np.ndarray:
        from . import retrieval
        return retrieval.embed_many(texts)

    def embed_one(self, text: str) -> np.ndarray:
        from . import retrieval
        return retrieval.embed(text)


# --- Multinomial logistic regression (numpy) ---------------------------------

def _softmax(logits: np.ndarray) -> np.ndarray:
    z = logits - logits.max(axis=-1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=-1, keepdims=True)


class SoftmaxRegression:
    """A tiny multinomial logistic regression over fixed embeddings."""

    def __init__(self, classes: "list[str]", W: np.ndarray, b: np.ndarray,
                 embedder_id: str) -> None:
        self.classes = list(classes)
        self.W = W                      # (C, d)
        self.b = b                      # (C,)
        self.embedder_id = embedder_id

    @classmethod
    def fit(cls, X: np.ndarray, labels: "list[str]", embedder_id: str, *,
            epochs: int = 600, lr: float = 5.0, l2: float = 1e-3,
            classes: "list[str] | None" = None) -> "SoftmaxRegression":
        X = np.asarray(X, dtype=np.float64)
        n, d = X.shape
        classes = list(classes) if classes else sorted(set(labels))
        idx = {c: i for i, c in enumerate(classes)}
        C = len(classes)
        Y = np.zeros((n, C), dtype=np.float64)
        for row, lbl in enumerate(labels):
            Y[row, idx[lbl]] = 1.0
        W = np.zeros((C, d), dtype=np.float64)
        b = np.zeros(C, dtype=np.float64)
        for _ in range(epochs):
            P = _softmax(X @ W.T + b)          # (n, C)
            diff = P - Y                        # (n, C)
            gW = diff.T @ X / n + l2 * W        # (C, d)
            gB = diff.mean(axis=0)              # (C,)
            W -= lr * gW
            b -= lr * gB
        return cls(classes, W, b, embedder_id)

    def predict_one(self, vec: np.ndarray) -> "tuple[str, float]":
        logits = self.W @ np.asarray(vec, dtype=np.float64) + self.b
        p = _softmax(logits)
        i = int(np.argmax(p))
        return self.classes[i], float(p[i])

    def save(self, path: str) -> None:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        np.savez(
            path, W=self.W.astype(np.float32), b=self.b.astype(np.float32),
            classes=np.array(self.classes, dtype=object),
            embedder_id=np.array(self.embedder_id),
            version=np.array(ARTIFACT_VERSION),
        )
        # np.savez appends .npz; normalize so callers find the file at `path`.
        if not os.path.exists(path) and os.path.exists(path + ".npz"):
            os.replace(path + ".npz", path)

    @classmethod
    def load(cls, path: str) -> "SoftmaxRegression":
        with np.load(path, allow_pickle=True) as z:
            if int(z["version"]) != ARTIFACT_VERSION:
                raise ValueError("intent model artifact version mismatch")
            classes = [str(c) for c in z["classes"].tolist()]
            return cls(classes, z["W"].astype(np.float64),
                       z["b"].astype(np.float64), str(z["embedder_id"]))


# --- Slot extraction for slotted intents -------------------------------------
# The classifier names the intent; for slotted intents the object is extracted
# deterministically here (then re-validated by build_match). Best-effort: an
# imperfect slot for an exotic phrasing at worst yields a slightly-wide query or
# an unresolved app name that build_match refuses — never an unsafe action.

import re  # noqa: E402  (kept local to this section for readability)

_SLOT_STRIP = {
    "open": re.compile(
        r"^(?:open(?:\s+up)?|launch|start(?:\s+up)?|fire\s+up|boot\s+up|load|"
        r"go\s+to|goto|navigate\s+to|take\s+me\s+to|visit|browse\s+to|"
        r"pull\s+up|bring\s+up|get\s+me\s+to)\s+(?:the\s+|my\s+|an?\s+)?", re.I),
    "web_search": re.compile(
        r"^(?:search\s+(?:the\s+web\s+|google\s+|the\s+internet\s+)?(?:for\s+)?|"
        r"google\s+|look\s+up\s+|lookup\s+|find\s+(?:me\s+)?)", re.I),
    "quick_note": re.compile(
        r"^(?:take\s+down\s+(?:that\s+)?|(?:take|make|add|create|write)\s+(?:a\s+|me\s+a\s+)?note(?:\s+(?:to\s+self|that|saying|about))?[:\s]+|"
        r"jot(?:\s+down)?(?:\s+this)?[:\s]+|note(?:\s+(?:to\s+self|that|saying|about|down))?[:\s]+|"
        r"quick\s+note[:\s]+|remember\s+(?:that\s+|to\s+)?)", re.I),
    "draft_event": re.compile(
        r"^(?:create|add|make|schedule|set\s+up|book|put)\s+(?:an?\s+)?"
        r"(?:calendar\s+)?(?:event|meeting|appointment|call|reminder)?\s*"
        r"(?:titled\s+|called\s+|for\s+|about\s+|on\s+)?", re.I),
}
_SEARCH_TAIL = re.compile(r"\s+(?:online|on\s+google|on\s+the\s+web|on\s+the\s+internet)\s*$", re.I)


def _extract_slot(label: str, body: str) -> str:
    """Pull the object out of a normalized body for a slotted intent."""
    strip = _SLOT_STRIP.get(label)
    if strip is None:
        return body.strip()
    slot = strip.sub("", body, count=1).strip()
    if label == "web_search":
        slot = _SEARCH_TAIL.sub("", slot).strip()
    return re.sub(r"\s+", " ", slot)


# --- The predictor -----------------------------------------------------------

class EmbeddingPredictor:
    """Predictor backed by the embedding classifier. Implements the same
    ``.predict(body) -> Prediction`` contract as ``KeywordPredictor``."""

    def __init__(self, embedder=None, artifact_path: "str | None" = None,
                 seed=SEED) -> None:
        self._embedder = embedder                    # None → lazy RepoEmbedder
        self._path = artifact_path or DEFAULT_ARTIFACT_PATH
        self._seed = seed
        self._model: "SoftmaxRegression | None" = None
        self._ready = False

    def _get_embedder(self):
        if self._embedder is None:
            self._embedder = RepoEmbedder()
        return self._embedder

    def _ensure_ready(self) -> None:
        if self._ready:
            return
        emb = self._get_embedder()
        emb_id = emb.name()
        # Reuse a cached artifact only if it was built with the same embedder.
        if self._path and os.path.isfile(self._path):
            try:
                clf = SoftmaxRegression.load(self._path)
                if clf.embedder_id == emb_id:
                    self._model = clf
                    self._ready = True
                    return
            except Exception:   # noqa: BLE001 — stale/corrupt cache → retrain
                pass
        texts = [t for t, _ in self._seed]
        labels = [lbl for _, lbl in self._seed]
        X = emb.embed_many(texts)
        self._model = SoftmaxRegression.fit(X, labels, emb_id)
        if self._path:
            try:
                self._model.save(self._path)
            except Exception:   # noqa: BLE001 — caching is best-effort
                pass
        self._ready = True

    def warm(self) -> None:
        """Force the embedder + model to load now (e.g. from a warmup thread)."""
        try:
            self._ensure_ready()
        except Exception:   # noqa: BLE001 — warmup must never crash the app
            pass

    def predict(self, body: str) -> "_im.Prediction":
        try:
            self._ensure_ready()
            text = _im.normalize_command(body)
            if not text or self._model is None:
                return _im._NONE
            vec = self._get_embedder().embed_one(text)
            label, prob = self._model.predict_one(vec)
            spec = LABEL_SPEC.get(label)
            if spec is None:                 # 'none' (abstain) or unknown label
                return _im._NONE
            handler, fixed_slot = spec
            if fixed_slot is None:           # slotted → extract, else abstain
                slot = _extract_slot(label, text)
                if not slot:
                    return _im._NONE
            else:
                slot = fixed_slot
            return _im.Prediction(handler, slot, float(prob))
        except Exception:   # noqa: BLE001 — a predictor must never break dictation
            return _im._NONE


# --- Module-cached predictor (load-once seam) --------------------------------

_MODEL_PREDICTOR: "EmbeddingPredictor | None" = None
_MODEL_PATH: "str | None" = None


def get_model_predictor(artifact_path: "str | None" = None) -> EmbeddingPredictor:
    """Return the process-wide EmbeddingPredictor, constructing (not loading) it
    once per artifact path. The heavy embedder/model load happens lazily on the
    first ``predict``."""
    global _MODEL_PREDICTOR, _MODEL_PATH
    path = artifact_path or DEFAULT_ARTIFACT_PATH
    if _MODEL_PREDICTOR is None or _MODEL_PATH != path:
        _MODEL_PREDICTOR = EmbeddingPredictor(artifact_path=path)
        _MODEL_PATH = path
    return _MODEL_PREDICTOR


def reset_model_predictor() -> None:
    """Drop the cached predictor (tests / after retraining)."""
    global _MODEL_PREDICTOR, _MODEL_PATH
    _MODEL_PREDICTOR = None
    _MODEL_PATH = None
