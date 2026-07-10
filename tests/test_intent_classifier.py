"""Phase 14+ — the embedding intent classifier (ML head).

Uses a deterministic bag-of-words FAKE embedder so the pipeline (train →
predict → slot-extract → build_match → gate) is exercised fast and without
loading sentence-transformers. Real semantic accuracy is validated separately
via scripts/train_intent.py --eval. The load-bearing safety property is the
same as the keyword predictor's: a model prediction only ever resolves to an
ActionMatch that build_match — the same allowlist/URL guards as the regex path
— permits.
"""
from __future__ import annotations

import re
import zlib

import numpy as np
import pytest

from src import intent_classifier as ic
from src import intent_model as im


# --- a deterministic, dependency-free stand-in for the ST embedder -----------

class FakeEmbedder:
    """Hashing bag-of-words → L2-normalized vector. Shares-tokens ⇒ similar,
    which is enough to train the LR on the seed and classify token-overlapping
    utterances. NOT semantic (no synonym generalization) — that's the real
    embedder's job, measured elsewhere."""

    def __init__(self, dim: int = 128) -> None:
        self.dim = dim

    def name(self) -> str:
        return "fake-bow-v1"

    def embed_one(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for tok in re.findall(r"[a-z0-9]+", (text or "").lower()):
            v[zlib.crc32(tok.encode()) % self.dim] += 1.0
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else v

    def embed_many(self, texts):
        return np.vstack([self.embed_one(t) for t in texts])


@pytest.fixture(autouse=True)
def _reset_caches():
    ic.reset_model_predictor()
    im.set_predictor(None)
    yield
    ic.reset_model_predictor()
    im.set_predictor(None)


def _cfg(apps=None, folders=None):
    exp = {}
    if apps is not None:
        exp["action_apps"] = apps
    if folders is not None:
        exp["action_folders"] = folders
    return {"experimental": exp}


APPS = {"spotify": "spotify", "notepad": "notepad.exe"}


# --- SoftmaxRegression -------------------------------------------------------

def test_softmax_regression_learns_separable_classes():
    # Three clearly separable one-hot-ish clusters.
    X = np.array([[1, 0, 0], [0.9, 0.1, 0], [0, 1, 0], [0.1, 0.9, 0],
                  [0, 0, 1], [0, 0.1, 0.9]], dtype=np.float64)
    y = ["a", "a", "b", "b", "c", "c"]
    clf = ic.SoftmaxRegression.fit(X, y, "fake")
    assert clf.predict_one(np.array([1.0, 0, 0]))[0] == "a"
    assert clf.predict_one(np.array([0, 1.0, 0]))[0] == "b"
    assert clf.predict_one(np.array([0, 0, 1.0]))[0] == "c"
    label, prob = clf.predict_one(np.array([1.0, 0, 0]))
    assert 0.0 <= prob <= 1.0


def test_softmax_regression_save_load_roundtrip(tmp_path):
    X = np.array([[1, 0], [0, 1], [0.9, 0.1], [0.1, 0.9]], dtype=np.float64)
    y = ["x", "y", "x", "y"]
    clf = ic.SoftmaxRegression.fit(X, y, "fake-emb")
    path = str(tmp_path / "m.npz")
    clf.save(path)
    loaded = ic.SoftmaxRegression.load(path)
    assert loaded.classes == clf.classes
    assert loaded.embedder_id == "fake-emb"
    for vec in ([1.0, 0], [0, 1.0]):
        assert loaded.predict_one(np.array(vec))[0] == clf.predict_one(np.array(vec))[0]


# --- slot extraction ---------------------------------------------------------

@pytest.mark.parametrize("label,body,slot", [
    ("open", "launch spotify", "spotify"),
    ("open", "navigate to github.com", "github.com"),
    ("open", "open the calculator", "calculator"),
    ("web_search", "google best tacos", "best tacos"),
    ("web_search", "search the web for cheap flights", "cheap flights"),
    ("web_search", "look up the weather online", "the weather"),
    ("quick_note", "jot down buy milk", "buy milk"),
    ("quick_note", "remember to call mom", "call mom"),
    ("draft_event", "create an event lunch with sam tomorrow", "lunch with sam tomorrow"),
])
def test_extract_slot(label, body, slot):
    assert ic._extract_slot(label, im.normalize_command(body)) == slot


# --- EmbeddingPredictor (trained on seed via the fake embedder) --------------

def _trained_predictor(tmp_path):
    return ic.EmbeddingPredictor(embedder=FakeEmbedder(),
                                 artifact_path=str(tmp_path / "m.npz"))


def test_predictor_trains_and_caches_artifact(tmp_path):
    path = tmp_path / "m.npz"
    p = ic.EmbeddingPredictor(embedder=FakeEmbedder(), artifact_path=str(path))
    p.predict("launch spotify")                 # triggers lazy train + cache
    assert path.exists()


@pytest.mark.parametrize("body,handler,slot", [
    ("launch spotify",     "open",       "spotify"),
    ("play some music",    "media_key",  "playpause"),
    ("skip this song",     "media_key",  "nexttrack"),
    ("turn up the volume", "volume",     "up"),
    ("mute the sound",     "media_key",  "volumemute"),
])
def test_predictor_recovers_seed_phrasings(tmp_path, body, handler, slot):
    p = _trained_predictor(tmp_path)
    pred = p.predict(body)
    assert pred.handler == handler and pred.slot == slot
    assert 0.0 <= pred.confidence <= 1.0


def test_predictor_abstains_on_plain_dictation(tmp_path):
    p = _trained_predictor(tmp_path)
    assert p.predict("the weather is beautiful this afternoon").handler == "none"


@pytest.mark.parametrize("body,expected", [
    ("  Launch Spotify.  ", "launch spotify"),
    ("Can you please open GitHub.com?", "open github.com"),
    ("PLAY SOME MUSIC", "play some music"),
])
def test_prepare_text_is_train_serve_transform(body, expected):
    # The embedder must see the same transform at train and inference time.
    assert ic.prepare_text(body) == expected


def test_predictor_case_and_punctuation_insensitive(tmp_path):
    # A live transcript with caps/punctuation must classify like the clean form.
    p = _trained_predictor(tmp_path)
    assert p.predict("Launch Spotify!").handler == p.predict("launch spotify").handler
    assert p.predict("Launch Spotify!").slot == "Spotify"   # slot keeps original case


@pytest.mark.parametrize("junk", ["", "   ", None])
def test_predictor_never_raises_on_junk(tmp_path, junk):
    p = _trained_predictor(tmp_path)
    assert p.predict(junk).handler == "none"


def test_predictor_survives_broken_embedder(tmp_path):
    class Boom:
        def name(self): return "boom"
        def embed_many(self, t): raise RuntimeError("no model")
        def embed_one(self, t): raise RuntimeError("no model")
    p = ic.EmbeddingPredictor(embedder=Boom(), artifact_path=str(tmp_path / "m.npz"))
    assert p.predict("launch spotify").handler == "none"


def test_predictor_abstains_when_slot_unextractable(tmp_path, monkeypatch):
    # A slotted intent whose object extracts to empty must abstain, not emit an
    # empty-slot action. (Defensive: the strips rarely yield empty, so drive it
    # directly.)
    class Stub:
        def predict_one(self, vec): return ("web_search", 0.99)
    p = _trained_predictor(tmp_path)
    p._ready = True
    p._model = Stub()
    monkeypatch.setattr(ic, "_extract_slot", lambda label, body: "")
    assert p.predict("some slotted phrasing").handler == "none"


# --- end-to-end through the spine: prediction → build_match → ActionMatch ----

def test_model_prediction_flows_through_build_match(tmp_path):
    # A seed phrasing, model backend, validated against a real allowlist.
    r = im.infer("launch spotify", _cfg(apps=APPS),
                 predictor=_trained_predictor(tmp_path), min_conf=0.3)
    assert r.match is not None and r.match.name == "open_app"
    assert r.match.args == {"app": "spotify"}


def test_model_cannot_launch_unconfigured_app(tmp_path):
    # THE safety property holds for the ML head too: an app the model proposes
    # but the allowlist doesn't contain resolves to nothing.
    r = im.infer("launch spotify", _cfg(apps={}),
                 predictor=_trained_predictor(tmp_path), min_conf=0.3)
    assert r.match is None


def test_model_slotless_intent_resolves(tmp_path):
    # "crank it up" not "crank the tunes": under the BOW fake embedder "tunes"
    # shares no tokens with the volume class, so it abstains — only the real
    # embedder generalizes it (covered by scripts/train_intent.py --eval). The
    # point here is the slotless intent → fixed-slot ActionMatch path.
    r = im.infer("crank it up", _cfg(),
                 predictor=_trained_predictor(tmp_path), min_conf=0.3)
    assert r.match is not None and r.match.name == "volume"
    assert r.match.args == {"dir": "up"}


# --- backend selection -------------------------------------------------------

def test_backend_defaults_to_keyword():
    p = im._predictor_for_cfg(_cfg())
    assert isinstance(p, im.KeywordPredictor)


def test_backend_model_selected(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(ic, "get_model_predictor", lambda path=None: sentinel)
    cfg = {"experimental": {"action_intent_backend": "model"}}
    assert im._predictor_for_cfg(cfg) is sentinel


def test_backend_model_failure_falls_back_to_keyword(monkeypatch):
    def boom(path=None): raise RuntimeError("no deps")
    monkeypatch.setattr(ic, "get_model_predictor", boom)
    cfg = {"experimental": {"action_intent_backend": "model"}}
    assert isinstance(im._predictor_for_cfg(cfg), im.KeywordPredictor)


# --- warm-thread concurrency (MODEL-SHADOW warmup) ----------------------------

def test_ensure_ready_is_single_flight_across_threads(tmp_path):
    """warm_in_background races the first live predict into _ensure_ready; the
    seed fit must happen exactly ONCE and the artifact write must be atomic
    (no partial/tmp files left for a concurrent load to trip on)."""
    import threading
    import time

    class SlowCountingEmbedder(FakeEmbedder):
        def __init__(self):
            super().__init__()
            self.fits = 0

        def embed_many(self, texts):
            self.fits += 1
            time.sleep(0.05)          # widen the race window
            return super().embed_many(texts)

    emb = SlowCountingEmbedder()
    p = ic.EmbeddingPredictor(embedder=emb, artifact_path=str(tmp_path / "m.npz"))
    threads = [threading.Thread(target=p.warm) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert emb.fits == 1
    # Exactly the finished artifact on disk — no orphaned tmp files.
    assert [f.name for f in tmp_path.iterdir()] == ["m.npz"]
