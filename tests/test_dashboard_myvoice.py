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


# --- POST /myvoice/humanize — the paste-in de-AI humanizer -------------------
#
# The page's primary action: paste AI-written prose, get it back in the user's
# voice. Routes to Cleaner.humanize_text, which returns (result, reason) so a
# refusal can be explained rather than dead-ending on "no confident rewrite".

AI_IN = "Moreover, it is a testament to our robust and evolving landscape."
MINE = "It says a lot about how we build things."


def test_myvoice_humanize_shows_result(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = (MINE, "ok")
    client, _, h = _client(tmp_path, cleaner=cleaner)
    vs.add_sample(h.conn, "how I write, sample text")

    r = client.post("/myvoice/humanize", headers=HDR, data={"hz_text": AI_IN})

    assert b"In your voice" in r.data
    assert MINE.encode() in r.data
    assert cleaner.humanize_text.call_args.args[0] == AI_IN


def test_myvoice_humanize_threads_config_knobs(tmp_path):
    """The paste-in path must use its OWN timeout and floor — the dictation
    8s/0.85 values would time out and then reject every real rewrite."""
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = (MINE, "ok")
    client, app_ref, h = _client(tmp_path, cleaner=cleaner)
    app_ref.cfg["experimental"].update({
        "humanize_text_timeout_sec": 60.0,
        "humanize_text_min_sim": 0.5,
        "humanize_text_max_chars": 1234,
    })
    vs.add_sample(h.conn, "sample")

    client.post("/myvoice/humanize", headers=HDR, data={"hz_text": AI_IN})

    kw = cleaner.humanize_text.call_args.kwargs
    assert kw["timeout_sec"] == 60.0
    assert kw["min_sim"] == 0.5
    assert kw["max_chars"] == 1234


def test_myvoice_humanize_without_samples_prompts_for_one(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    client, _, _ = _client(tmp_path, cleaner=cleaner)     # no samples, no profile

    r = client.post("/myvoice/humanize", headers=HDR, data={"hz_text": AI_IN})

    assert b"add a writing sample below" in r.data.lower()
    cleaner.humanize_text.assert_not_called()             # skipped: empty profile


def test_myvoice_humanize_refuses_oversize_paste_without_calling_model(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    client, app_ref, h = _client(tmp_path, cleaner=cleaner)
    app_ref.cfg["experimental"]["humanize_text_max_chars"] = 50
    vs.add_sample(h.conn, "sample")

    r = client.post("/myvoice/humanize", headers=HDR, data={"hz_text": "x" * 200})

    assert b"over the 50-character limit" in r.data
    cleaner.humanize_text.assert_not_called()


@pytest.mark.parametrize("reason, needle", [
    ("provider_down", b"Is Ollama running"),
    ("meaning_drift", b"drifted from what your text actually said"),
    ("bad_shape", "didn’t return a clean rewrite".encode()),
    ("unchanged", b"already reads like you"),
])
def test_myvoice_humanize_explains_each_refusal(tmp_path, reason, needle):
    """Every refusal reason gets its own message — the whole point of returning
    (result, reason) instead of None."""
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = (None, reason)
    client, _, h = _client(tmp_path, cleaner=cleaner)
    vs.add_sample(h.conn, "sample")

    r = client.post("/myvoice/humanize", headers=HDR, data={"hz_text": AI_IN})

    assert needle in r.data


def test_myvoice_humanize_survives_a_raising_cleaner(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.side_effect = RuntimeError("boom")
    client, _, h = _client(tmp_path, cleaner=cleaner)
    vs.add_sample(h.conn, "sample")

    r = client.post("/myvoice/humanize", headers=HDR, data={"hz_text": AI_IN})

    assert r.status_code == 200
    assert b"Is Ollama running" in r.data


def test_myvoice_humanize_flags_a_partial_rewrite(tmp_path):
    """A half-rewritten document must say so, or the untouched paragraphs read
    as a deliberate choice rather than a rejected rewrite."""
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = (MINE + "\n\nUntouched paragraph.", "partial")
    client, _, h = _client(tmp_path, cleaner=cleaner)
    vs.add_sample(h.conn, "sample")

    r = client.post("/myvoice/humanize", headers=HDR, data={"hz_text": AI_IN})

    assert b"Some paragraphs are unchanged" in r.data
    assert b"Untouched paragraph." in r.data


def test_myvoice_humanize_renders_a_word_diff(tmp_path):
    """A rewrite you can't inspect has to be trusted blindly, so a successful
    pass ships its change set with the text."""
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = ("The quick red fox jumped.", "ok")
    client, _, h = _client(tmp_path, cleaner=cleaner)
    vs.add_sample(h.conn, "sample")

    r = client.post("/myvoice/humanize", headers=HDR,
                    data={"hz_text": "The quick brown fox jumped."})

    assert b"What changed" in r.data
    assert b"<del" in r.data and b"brown" in r.data      # removed word struck out
    assert b"<ins" in r.data and b"red" in r.data        # replacement highlighted


def test_myvoice_no_diff_when_nothing_came_back(tmp_path):
    from unittest.mock import MagicMock
    cleaner = MagicMock()
    cleaner.humanize_text.return_value = (None, "meaning_drift")
    client, _, h = _client(tmp_path, cleaner=cleaner)
    vs.add_sample(h.conn, "sample")

    r = client.post("/myvoice/humanize", headers=HDR, data={"hz_text": AI_IN})

    assert b"What changed" not in r.data
