"""/myvoice dashboard page — sample CRUD + shadow review rendering."""
from __future__ import annotations

import pytest

from src.history import History
from src.dashboard import voice_samples as vs
from src import voice_profile as vp


HDR = {"Host": "127.0.0.1:8766"}


@pytest.fixture(autouse=True)
def _clear_profile_cache():
    # voice_profile caches the profile in module-level state; clear it around
    # each test so a sample added directly (not via the route) is seen.
    vp.invalidate()
    yield
    vp.invalidate()


def _h(tmp_path):
    return History(str(tmp_path / "h.db"))


class _App:
    def __init__(self, history, humanize="shadow", cleaner=None):
        self.cfg = {"dashboard": {"host": "127.0.0.1", "port": 8766},
                    "experimental": {"humanize": humanize}}
        self.history = history
        self.retriever = None
        self.cleaner = cleaner


def _client(tmp_path, humanize="shadow", cleaner=None):
    from src.dashboard.app import make_app
    h = _h(tmp_path)
    app_ref = _App(h, humanize, cleaner)
    return make_app(app_ref).test_client(), app_ref, h


def test_myvoice_get_empty(tmp_path):
    client, _, _ = _client(tmp_path)
    r = client.get("/myvoice", headers=HDR)
    assert r.status_code == 200
    assert b"No samples yet" in r.data
    assert b"My Voice" in r.data


def test_myvoice_add_and_delete(tmp_path):
    client, _, h = _client(tmp_path)
    client.post("/myvoice/add", headers=HDR,
                data={"content": "This is exactly how I write things."})
    r = client.get("/myvoice", headers=HDR)
    assert b"exactly how I write" in r.data
    sid = vs.list_samples(h.conn)[0]["id"]
    client.post("/myvoice/delete", headers=HDR, data={"id": str(sid)})
    assert vs.list_samples(h.conn) == []


def test_myvoice_toggle(tmp_path):
    client, _, h = _client(tmp_path)
    sid = vs.add_sample(h.conn, "toggle me")
    client.post("/myvoice/toggle", headers=HDR, data={"id": str(sid), "enabled": "0"})
    assert vs.enabled_texts(h.conn) == []
    client.post("/myvoice/toggle", headers=HDR, data={"id": str(sid), "enabled": "1"})
    assert vs.enabled_texts(h.conn) == ["toggle me"]


def test_myvoice_import(tmp_path):
    client, _, h = _client(tmp_path)
    client.post("/myvoice/import", headers=HDR,
                data={"bulk": "block one\n\nblock two"})
    assert len(vs.list_samples(h.conn)) == 2


def test_myvoice_renders_shadow_rows(tmp_path):
    client, _, h = _client(tmp_path)
    h.log_humanize_shadow(cleaned_text="the cleaned sentence",
                          humanized_text="the sentence, my way",
                          style="polished", similarity=0.92)
    r = client.get("/myvoice", headers=HDR)
    assert b"Shadow preview" in r.data
    assert b"the sentence, my way" in r.data
    assert b"0.92" in r.data


def test_myvoice_in_sidebar(tmp_path):
    client, _, _ = _client(tmp_path)
    r = client.get("/myvoice", headers=HDR)
    assert b'href="/myvoice"' in r.data


def test_myvoice_renders_profile_preview(tmp_path):
    client, _, h = _client(tmp_path)
    vs.add_sample(h.conn, "a distinctive phrase I always use")
    r = client.get("/myvoice", headers=HDR)
    assert b"What Echo Flow learned" in r.data
    assert b"a distinctive phrase I always use" in r.data


# --- POST /myvoice/humanize — the paste-in humanizer (v2: modes + always-show) -
#
# Routes to Cleaner.humanize_text, which returns a HumanizeOutcome. Three modes
# (human / voice / tone), always returns a result, warnings surfaced in the UI.

from src.cleanup import HumanizeOutcome

AI_IN = "Moreover, it is a testament to our robust and evolving landscape."
MINE = "It says a lot about how we build things."


def _outcome(text, reason="ok", warnings=None, changed=1, total=1):
    return HumanizeOutcome(text, reason, warnings or [], changed, total)


def test_humanize_shows_result_without_any_samples(tmp_path):
    """The default (human) mode works with zero samples — the whole point."""
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = _outcome(MINE)
    client, _, _ = _client(tmp_path, cleaner=cleaner)     # no samples added

    r = client.post("/myvoice/humanize", headers=HDR, data={"hz_text": AI_IN})

    assert b"Humanized" in r.data
    assert MINE.encode() in r.data
    assert cleaner.humanize_text.call_args.args[0] == AI_IN
    assert cleaner.humanize_text.call_args.kwargs["mode"] == "human"


def test_humanize_threads_mode_and_tone_from_the_form(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = _outcome(MINE)
    client, _, _ = _client(tmp_path, cleaner=cleaner)

    client.post("/myvoice/humanize", headers=HDR,
                data={"hz_text": AI_IN, "hz_mode": "tone", "hz_tone": "casual"})

    kw = cleaner.humanize_text.call_args.kwargs
    assert kw["mode"] == "tone" and kw["tone"] == "casual"


def test_humanize_only_builds_the_profile_in_voice_mode(tmp_path):
    """'human' and 'tone' modes need no samples, so the profile is passed empty
    and voice mode gets the real thing."""
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = _outcome(MINE)
    client, _, h = _client(tmp_path, cleaner=cleaner)
    vs.add_sample(h.conn, "how I actually write things, in my own words")

    client.post("/myvoice/humanize", headers=HDR,
                data={"hz_text": AI_IN, "hz_mode": "human"})
    assert cleaner.humanize_text.call_args.kwargs["voice_profile"] == ""

    client.post("/myvoice/humanize", headers=HDR,
                data={"hz_text": AI_IN, "hz_mode": "voice"})
    assert cleaner.humanize_text.call_args.kwargs["voice_profile"] != ""


def test_humanize_rejects_a_bad_mode(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = _outcome(MINE)
    client, _, _ = _client(tmp_path, cleaner=cleaner)
    client.post("/myvoice/humanize", headers=HDR,
                data={"hz_text": AI_IN, "hz_mode": "../etc"})
    assert cleaner.humanize_text.call_args.kwargs["mode"] == "human"   # fell back


def test_humanize_threads_config_knobs(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = _outcome(MINE)
    client, app_ref, _ = _client(tmp_path, cleaner=cleaner)
    app_ref.cfg["experimental"].update({
        "humanize_text_timeout_sec": 60.0,
        "humanize_text_min_sim": 0.5,
        "humanize_text_max_chars": 1234,
        "humanize_text_model": "qwen3.5:latest",
    })

    client.post("/myvoice/humanize", headers=HDR, data={"hz_text": AI_IN})

    kw = cleaner.humanize_text.call_args.kwargs
    assert kw["timeout_sec"] == 60.0 and kw["min_sim"] == 0.5
    assert kw["max_chars"] == 1234 and kw["model"] == "qwen3.5:latest"


def test_humanize_renders_warnings(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = _outcome(
        MINE, reason="warned",
        warnings=["A number may have changed — double-check the figures in the text."])
    client, _, _ = _client(tmp_path, cleaner=cleaner)

    r = client.post("/myvoice/humanize", headers=HDR, data={"hz_text": AI_IN})

    assert MINE.encode() in r.data
    assert "double-check the figures".encode() in r.data


def test_humanize_kept_shows_original_with_a_note(tmp_path):
    """When nothing could be rewritten the original is shown (always-show), with
    a note explaining why — never a dead end."""
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = _outcome(AI_IN, reason="kept", changed=0)
    client, _, _ = _client(tmp_path, cleaner=cleaner)

    r = client.post("/myvoice/humanize", headers=HDR, data={"hz_text": AI_IN})

    assert AI_IN.encode() in r.data
    assert b"Couldn" in r.data                      # "Couldn't produce a clean rewrite"
    assert b"What changed" not in r.data            # no diff when text == input


def test_humanize_refuses_oversize_paste_without_calling_model(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    client, app_ref, _ = _client(tmp_path, cleaner=cleaner)
    app_ref.cfg["experimental"]["humanize_text_max_chars"] = 50

    r = client.post("/myvoice/humanize", headers=HDR, data={"hz_text": "x" * 200})

    assert b"over the 50-character limit" in r.data
    cleaner.humanize_text.assert_not_called()


def test_humanize_provider_down_message(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = HumanizeOutcome(None, "provider_down")
    client, _, _ = _client(tmp_path, cleaner=cleaner)

    r = client.post("/myvoice/humanize", headers=HDR, data={"hz_text": AI_IN})

    assert b"Is Ollama running" in r.data


def test_humanize_survives_a_raising_cleaner(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.side_effect = RuntimeError("boom")
    client, _, _ = _client(tmp_path, cleaner=cleaner)

    r = client.post("/myvoice/humanize", headers=HDR, data={"hz_text": AI_IN})

    assert r.status_code == 200
    assert b"Is Ollama running" in r.data


def test_humanize_renders_a_word_diff_when_text_changed(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = _outcome("The quick red fox jumped.")
    client, _, _ = _client(tmp_path, cleaner=cleaner)

    r = client.post("/myvoice/humanize", headers=HDR,
                    data={"hz_text": "The quick brown fox jumped."})

    assert b"What changed" in r.data
    assert b"<del" in r.data and b"brown" in r.data
    assert b"<ins" in r.data and b"red" in r.data


def test_humanize_page_has_mode_and_tone_controls(tmp_path):
    client, _, _ = _client(tmp_path)
    r = client.get("/myvoice", headers=HDR)
    assert b'name="hz_mode"' in r.data
    assert b'name="hz_tone"' in r.data
    assert b'value="human"' in r.data and b'value="voice"' in r.data and b'value="tone"' in r.data



def test_humanize_threads_strength(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = _outcome(MINE)
    client, _, _ = _client(tmp_path, cleaner=cleaner)
    client.post("/myvoice/humanize", headers=HDR,
                data={"hz_text": AI_IN, "hz_strength": "aggressive"})
    assert cleaner.humanize_text.call_args.kwargs["strength"] == "aggressive"


def test_humanize_bad_strength_falls_back(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = _outcome(MINE)
    client, _, _ = _client(tmp_path, cleaner=cleaner)
    client.post("/myvoice/humanize", headers=HDR,
                data={"hz_text": AI_IN, "hz_strength": "nonsense"})
    assert cleaner.humanize_text.call_args.kwargs["strength"] == "balanced"


def test_humanize_uses_custom_tone_text(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = _outcome(MINE)
    client, _, _ = _client(tmp_path, cleaner=cleaner)
    client.post("/myvoice/humanize", headers=HDR, data={
        "hz_text": AI_IN, "hz_mode": "tone",
        "hz_tone": "custom", "hz_tone_custom": "dry and witty"})
    kw = cleaner.humanize_text.call_args.kwargs
    assert kw["mode"] == "tone" and kw["tone"] == "dry and witty"


def test_humanize_threads_escalate_model_from_config(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = _outcome(MINE)
    client, app_ref, _ = _client(tmp_path, cleaner=cleaner)
    app_ref.cfg["experimental"]["humanize_text_escalate_model"] = "qwen3.5:latest"
    client.post("/myvoice/humanize", headers=HDR, data={"hz_text": AI_IN})
    assert cleaner.humanize_text.call_args.kwargs["escalate_model"] == "qwen3.5:latest"


def test_humanize_shows_the_ai_tell_score(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    # A telly input, a clean output → score should drop, and be shown.
    cleaner.humanize_text.return_value = _outcome("We shipped it and it works.")
    client, _, _ = _client(tmp_path, cleaner=cleaner)
    telly = "Moreover, this is a testament to our robust, seamless synergy."
    r = client.post("/myvoice/humanize", headers=HDR, data={"hz_text": telly})
    assert b"AI tells:" in r.data


def test_humanize_page_has_strength_and_custom_tone_controls(tmp_path):
    client, _, _ = _client(tmp_path)
    r = client.get("/myvoice", headers=HDR)
    assert b'name="hz_strength"' in r.data
    assert b'value="aggressive"' in r.data
    assert b'name="hz_tone_custom"' in r.data
    assert b"Try again" in r.data or b"hz-form" in r.data


def test_humanize_highlights_remaining_tells_in_the_output(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    # Output that still contains a tell → it should be <mark>ed inline.
    cleaner.humanize_text.return_value = _outcome("We leverage the new tool daily.")
    client, _, _ = _client(tmp_path, cleaner=cleaner)
    r = client.post("/myvoice/humanize", headers=HDR,
                    data={"hz_text": "Moreover, we use it."})
    assert b"<mark" in r.data and b"leverage" in r.data


def test_humanize_shows_tells_in_the_paste(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = _outcome("We use it every day.")
    client, _, _ = _client(tmp_path, cleaner=cleaner)
    r = client.post("/myvoice/humanize", headers=HDR,
                    data={"hz_text": "Moreover, we leverage seamless synergy."})
    assert b"AI tells in your paste" in r.data


def test_humanize_fetch_returns_only_the_result_fragment(tmp_path):
    """The async submit (fetch=1) gets just the result panel — no full page,
    no nav — so JS can drop it into the result box in place."""
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = _outcome("We shipped it and it works.")
    client, _, _ = _client(tmp_path, cleaner=cleaner)

    r = client.post("/myvoice/humanize", headers=HDR,
                    data={"hz_text": "Moreover, we leverage it.", "fetch": "1"})

    assert b"hz-output" in r.data                 # the result panel is present
    assert b"<html" not in r.data                 # but NOT the whole page
    assert b'href="/myvoice"' not in r.data        # no sidebar/nav


def test_humanize_normal_post_still_returns_full_page(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = _outcome("We shipped it.")
    client, _, _ = _client(tmp_path, cleaner=cleaner)

    r = client.post("/myvoice/humanize", headers=HDR,
                    data={"hz_text": "Moreover, we leverage it."})
    assert b"hz-result-card" in r.data or b"hz-output" in r.data
    assert b'name="hz_mode"' in r.data             # the full form is there too


def test_humanize_shows_side_by_side_compare(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = _outcome("We shipped it and it works.")
    client, _, _ = _client(tmp_path, cleaner=cleaner)

    r = client.post("/myvoice/humanize", headers=HDR,
                    data={"hz_text": "Moreover, we leverage the thing."})
    assert b"Compare" in r.data
    assert b"Original (AI)" in r.data and b"Humanized" in r.data


def test_humanize_shows_diagnostic_flags(tmp_path):
    """The 'specify' pass surfaces vague claims in the SOURCE as questions the
    writer must answer — a humanizer can't invent the fact."""
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = _outcome("A cleaned-up rewrite here.")
    client, _, _ = _client(tmp_path, cleaner=cleaner)

    r = client.post("/myvoice/humanize", headers=HDR, data={
        "hz_text": "This achieved significant improvements. "
                   "Researchers have shown it works."})

    assert b"Reads empty" in r.data
    assert b"significant improvements" in r.data
    assert b"Which study or source" in r.data


def test_humanize_no_diagnostics_when_source_is_concrete(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = _outcome("A rewrite.")
    client, _, _ = _client(tmp_path, cleaner=cleaner)

    r = client.post("/myvoice/humanize", headers=HDR, data={
        "hz_text": "We shipped the parser Tuesday; it reads 12 formats and "
                   "fails on rotated scans."})
    assert b"Reads empty" not in r.data


def test_humanize_shows_cut_sentences(tmp_path):
    """The delete-first pass reports what it removed, and the UI shows it."""
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = HumanizeOutcome(
        "Deep models win.", "ok", [], 1, 1,
        cut=["Machine learning has transformed the landscape of things."])
    client, _, _ = _client(tmp_path, cleaner=cleaner)

    r = client.post("/myvoice/humanize", headers=HDR, data={"hz_text": "x"})

    assert b"Cut 1 empty sentence" in r.data
    assert b"transformed the landscape" in r.data


def test_humanize_threads_delete_first_from_config(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = _outcome(MINE)
    client, app_ref, _ = _client(tmp_path, cleaner=cleaner)
    app_ref.cfg["experimental"]["humanize_text_delete_first"] = False
    client.post("/myvoice/humanize", headers=HDR, data={"hz_text": AI_IN})
    assert cleaner.humanize_text.call_args.kwargs["delete_first"] is False
