"""Phase 14+ — the local intent model (regex-miss Action Mode fallback).

The load-bearing property under test is the safety spine: whatever a predictor
proposes, :func:`build_match` re-validates it through the SAME allowlist / URL
guards as ``voice_actions.classify`` — so a prediction can never resolve to a
side effect the regex path wouldn't. Everything else (the keyword predictor,
the confidence floor, the length gate) is coverage around that invariant.
"""
from __future__ import annotations

import pytest

from src import intent_model as im
from src import voice_actions as va


def _cfg(apps=None, folders=None):
    exp = {}
    if apps is not None:
        exp["action_apps"] = apps
    if folders is not None:
        exp["action_folders"] = folders
    return {"experimental": exp}


APPS = {"spotify": "spotify", "notepad": "notepad.exe"}
FOLDERS = {"downloads": "%USERPROFILE%\\Downloads"}


# =============================================================================
# build_match — the safety spine (sole authority on what may execute)
# =============================================================================

def test_build_open_domain_becomes_open_url():
    m = im.build_match("open", "github.com", _cfg(apps=APPS))
    assert m is not None and m.name == "open_url"
    assert m.args == {"url": "https://github.com"}


def test_build_open_configured_app_becomes_open_app():
    m = im.build_match("open", "Spotify", _cfg(apps=APPS))
    assert m is not None and m.name == "open_app"
    assert m.args == {"app": "spotify"}      # lowercased key
    assert "Spotify" in m.label              # original case preserved in label


def test_build_open_unknown_app_is_refused():
    # THE core safety property: a model proposing an unconfigured app resolves
    # to nothing — the allowlist, not the predictor, decides what runs.
    assert im.build_match("open", "hacktool", _cfg(apps=APPS)) is None


def test_build_open_app_direct_unknown_refused():
    assert im.build_match("open_app", "hacktool", _cfg(apps=APPS)) is None
    assert im.build_match("open_app", "spotify", _cfg(apps=APPS)) is not None


def test_build_open_url_rejects_non_domain():
    # "navigate to spotify" must NOT silently open an app or guess a TLD.
    assert im.build_match("open_url", "spotify", _cfg()) is None


@pytest.mark.parametrize("bad", [
    "javascript:alert(1)", "file:///etc/passwd", "http://a:b@evil.com",
])
def test_build_open_url_rejects_unsafe_slots(bad):
    # The slot flows through _domain_to_url/_is_safe_url — the same guards the
    # regex path uses — so unsafe schemes / userinfo never build a match.
    assert im.build_match("open_url", bad, _cfg()) is None


def test_build_open_folder_allowlist_only():
    assert im.build_match("open_folder", "downloads", _cfg(folders=FOLDERS)) is not None
    assert im.build_match("open_folder", "c:/windows", _cfg(folders=FOLDERS)) is None


def test_build_free_text_handlers_require_a_slot():
    for h in ("web_search", "quick_note", "draft_event"):
        assert im.build_match(h, "hello", _cfg()) is not None
        assert im.build_match(h, "", _cfg()) is None
        assert im.build_match(h, "   ", _cfg()) is None


def test_build_slotless_handlers_always_resolve():
    for h in ("summarize_focused", "open_clipboard_link"):
        m = im.build_match(h, "", _cfg())
        assert m is not None and m.name == h


@pytest.mark.parametrize("key,ok", [
    ("playpause", True), ("nexttrack", True), ("prevtrack", True),
    ("volumemute", True), ("bogus", False), ("", False),
])
def test_build_media_key_is_allowlisted(key, ok):
    assert (im.build_match("media_key", key, _cfg()) is not None) is ok


@pytest.mark.parametrize("d,ok", [("up", True), ("down", True), ("sideways", False)])
def test_build_volume_direction_is_allowlisted(d, ok):
    assert (im.build_match("volume", d, _cfg()) is not None) is ok


def test_build_unknown_handler_refused():
    assert im.build_match("rm_rf", "/", _cfg()) is None


def test_build_match_never_raises_on_junk():
    for h in ("open", "open_url", "open_app", "web_search", "media_key", "volume", "x"):
        # None slot, empty cfg, weird types must not blow up.
        assert im.build_match(h, None, {}) is None or True  # noqa: PT018


# =============================================================================
# KeywordPredictor — recover phrasings the anchored regex misses
# =============================================================================

@pytest.mark.parametrize("body,handler,slot", [
    ("launch spotify",          "open",        "spotify"),
    ("start up notepad",        "open",        "notepad"),
    ("fire up spotify",         "open",        "spotify"),
    ("navigate to github.com",  "open_url",    "github.com"),
    ("take me to docs.python.org", "open_url", "docs.python.org"),
    ("google best pizza near me", "web_search", "best pizza near me"),
    ("look up the weather",     "web_search",  "the weather"),
    ("jot down buy milk",       "quick_note",  "buy milk"),
    ("remember to call mom",    "quick_note",  "call mom"),
    ("play some music",         "media_key",   "playpause"),
    ("pause the music",         "media_key",   "playpause"),
    ("skip this song",          "media_key",   "nexttrack"),
    ("previous track",          "media_key",   "prevtrack"),
    ("mute the sound",          "media_key",   "volumemute"),
    ("turn it up",              "volume",      "up"),
    ("turn up the volume",      "volume",      "up"),
    ("make it louder",          "volume",      "up"),
    ("lower the volume",        "volume",      "down"),
    ("quieter",                 "volume",      "down"),
    ("summarize this",          "summarize_focused", ""),
    ("tldr this page",          "summarize_focused", ""),
    ("open the clipboard link", "open_clipboard_link", ""),
])
def test_predict_recovers_phrasing(body, handler, slot):
    p = im.KeywordPredictor().predict(body)
    assert p.handler == handler
    assert p.slot == slot


def test_predict_strips_leading_filler():
    p = im.KeywordPredictor().predict("can you please launch spotify")
    assert p.handler == "open" and p.slot == "spotify"


def test_predict_recovers_filler_prefixed_open():
    # classify()'s _RE_OPEN only fires when the body starts with "open"; a
    # leading filler defeats it, so the model must recover "… open <x>".
    r = im.infer("can you please open github.com", _cfg(apps=APPS))
    assert r.match is not None and r.match.name == "open_url"
    assert r.match.args == {"url": "https://github.com"}


def test_predict_strips_trailing_politeness():
    p = im.KeywordPredictor().predict("play some music please")
    assert p.handler == "media_key" and p.slot == "playpause"


@pytest.mark.parametrize("body,handler,slot", [
    ("launch spotify.",            "open",       "spotify"),
    ("navigate to github.com.",    "open_url",   "github.com"),
    ("play some music!",           "media_key",  "playpause"),
    ("jot down buy milk.",         "quick_note", "buy milk"),
    ("launch spotify, please.",    "open",       "spotify"),
])
def test_predict_strips_trailing_punctuation(body, handler, slot):
    # A trailing STT/cleanup period is the most common real phrasing and must
    # not leak into the slot (mirrors classify()'s rstrip). Regression: MED-1.
    p = im.KeywordPredictor().predict(body)
    assert p.handler == handler and p.slot == slot


def test_infer_recovers_command_with_trailing_period():
    r = im.infer("launch spotify.", _cfg(apps=APPS))
    assert r.match is not None and r.match.name == "open_app"


@pytest.mark.parametrize("body", [
    "googled the wrong address entirely",
    "googles headquarters are in california",
])
def test_predict_google_requires_word_boundary(body):
    # "google" must not match as a prefix of a longer word. Regression: LOW-1.
    assert im.KeywordPredictor().predict(body).handler == "none"


@pytest.mark.parametrize("body", [
    "the meeting went really well today",
    "i think we should refactor the parser",
    "hello there how are you doing",
    "my favorite color is blue",
    "",
    "the",
])
def test_predict_abstains_on_plain_dictation(body):
    assert im.KeywordPredictor().predict(body).handler == "none"


def test_remind_me_sits_below_default_floor():
    # "remind me …" is ambiguous (note vs event); it predicts quick_note but at
    # a confidence intentionally under DEFAULT_MIN_CONF, so it abstains unless
    # the user lowers the floor.
    p = im.KeywordPredictor().predict("remind me to call the dentist")
    assert p.handler == "quick_note"
    assert p.confidence < im.DEFAULT_MIN_CONF


# =============================================================================
# infer / classify_with_model — gate + re-validate, never raise
# =============================================================================

def test_infer_gated_and_resolvable_yields_match():
    r = im.infer("launch spotify", _cfg(apps=APPS))
    assert r.gated is True
    assert r.match is not None and r.match.name == "open_app"


def test_infer_gated_but_unresolvable_has_prediction_no_match():
    # Predicts open(spotify) with confidence, but spotify isn't configured →
    # re-validation refuses it. Prediction is preserved for shadow logging.
    r = im.infer("launch spotify", _cfg(apps={}))
    assert r.gated is True
    assert r.prediction.handler == "open"
    assert r.match is None


def test_infer_below_floor_does_not_build():
    r = im.infer("remind me to call mom", _cfg())
    assert r.prediction.handler == "quick_note"
    assert r.gated is False
    assert r.match is None


def test_infer_below_floor_can_be_unlocked_by_lower_conf():
    r = im.infer("remind me to call mom", _cfg(), min_conf=0.5)
    assert r.gated is True and r.match is not None and r.match.name == "quick_note"


def test_infer_abstains_on_plain_dictation():
    r = im.infer("the weather is nice today", _cfg())
    assert r.prediction.handler == "none" and r.match is None


@pytest.mark.parametrize("body", [
    "x" * (im.MAX_BODY_CHARS + 1),                     # too long by chars
    "launch " + " ".join(["word"] * im.MAX_BODY_WORDS),  # too long by words
])
def test_infer_length_pre_gate(body):
    r = im.infer(body, _cfg(apps=APPS))
    assert r.match is None and r.gated is False


@pytest.mark.parametrize("bad_conf", ["not-a-number", None, [0.7]])
def test_infer_tolerates_malformed_min_conf(bad_conf):
    # infer() promises never to raise; a bad config value falls back to the
    # default floor instead of crashing the dictation pipeline. Regression: MED-2.
    r = im.infer("launch spotify", _cfg(apps=APPS), min_conf=bad_conf)
    assert r.match is not None and r.match.name == "open_app"


def test_classify_with_model_is_the_match():
    m = im.classify_with_model("google cats", _cfg())
    assert m is not None and m.name == "web_search" and m.args == {"query": "cats"}
    assert im.classify_with_model("the sky is blue", _cfg()) is None


def test_infer_survives_a_broken_predictor():
    class Boom:
        def predict(self, body):
            raise RuntimeError("predictor exploded")
    r = im.infer("launch spotify", _cfg(apps=APPS), predictor=Boom())
    assert r.match is None and r.prediction.handler == "none"


def test_eval_harness_check_gate_passes():
    # The offline eval harness doubles as a CI regression guard: its --check
    # mode asserts precision/recall bars on the labeled fixture set. Run it so a
    # future predictor change that regresses precision fails here too.
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run(
        [sys.executable, str(root / "scripts" / "eval_intent.py"), "--check"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "[PASS]" in r.stdout


def test_set_predictor_dependency_injection():
    try:
        im.set_predictor(None)                      # reset cache
        sentinel = im.get_predictor()
        assert isinstance(sentinel, im.KeywordPredictor)

        class Fake:
            def predict(self, body):
                return im.Prediction("web_search", "injected", 0.99)
        im.set_predictor(Fake())
        m = im.classify_with_model("anything at all", _cfg())
        assert m is not None and m.args == {"query": "injected"}
    finally:
        im.set_predictor(None)                      # don't leak into other tests
