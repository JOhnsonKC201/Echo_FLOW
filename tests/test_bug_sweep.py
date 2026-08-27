"""Regression tests for the 2026-08 bug sweep.

Each test here pins a defect that shipped and was fixed. They are grouped by
the subsystem they cover, and every one of them fails against the code as it
stood before the corresponding fix.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _quiet_console(monkeypatch):
    """src.main prints "● REC" / "■ stop" through rich. Under pytest's captured
    cp1252 stdout those glyphs raise UnicodeEncodeError, and in the worker
    thread that surfaces as an unrelated test failure. Silence the console for
    these tests, none of them assert on console output."""
    import src.main as _main
    monkeypatch.setattr(_main, "console", MagicMock())


def _wait(pred, timeout: float = 3.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.005)
    return pred()


def _make_app(*, paused: bool = False):
    """Bare App shell with only what the toggle/dictation paths read."""
    from src.main import App
    app = App.__new__(App)
    app._active = False
    app._paused = paused
    app._cancelled = False
    app._state_lock = threading.Lock()
    app._press_title = None
    app.cfg = {"sound": None, "audio": {"sample_rate": 16000}}
    app.tray = MagicMock()
    app.recorder = MagicMock()
    app.injector = MagicMock()
    app.injector.focused_title.return_value = "FakeWindow"
    return app


# ---------------------------------------------------------------------------
# audio.py, Silero VAD was never actually running
# ---------------------------------------------------------------------------

class _Prob:
    def __init__(self, v): self._v = v
    def item(self): return self._v


class _FakeVad:
    """Stands in for Silero, which accepts EXACTLY one 512-sample window per
    call at 16 kHz and raises on anything else."""
    def __init__(self, prob=0.9):
        self.prob = prob
        self.sizes: list[int] = []
        self.resets = 0

    def reset_states(self):
        self.resets += 1

    def __call__(self, t, sr):
        n = int(t.shape[-1])
        self.sizes.append(n)
        if n != 512:
            raise RuntimeError(f"Provided number of samples is {n} (expected 512)")
        return _Prob(self.prob)


def _recorder(sample_rate: int = 16000):
    from src.audio import AudioConfig, Recorder
    rec = Recorder.__new__(Recorder)
    rec.cfg = AudioConfig(sample_rate=sample_rate)
    rec._vad_warned = False
    return rec


def test_is_voiced_feeds_silero_exactly_512_sample_windows():
    pytest.importorskip("torch")
    rec = _recorder()
    vad = _FakeVad(prob=0.9)
    # A realistic rolling window: ten 30 ms blocks of 480 frames = 4800 samples,
    # which is not a multiple of 512. The old code passed a flat 2048-sample
    # slice, so every call raised and silently fell back to RMS.
    sample = np.zeros(4800, dtype=np.float32)
    assert rec._is_voiced(sample, vad) is True
    assert vad.sizes, "Silero was never called"
    assert set(vad.sizes) == {512}


def test_is_voiced_returns_vad_verdict_not_rms():
    """Loud audio that Silero calls non-speech must be reported non-voiced.

    Before the fix the VAD call always raised and the RMS fallback answered,
    so a noisy room read as speech forever and toggle mode never auto-stopped.
    """
    pytest.importorskip("torch")
    rec = _recorder()
    loud_noise = np.full(4800, 0.4, dtype=np.float32)
    assert float(np.sqrt(np.mean(loud_noise ** 2))) > 0.01   # RMS would say "voiced"
    assert rec._is_voiced(loud_noise, _FakeVad(prob=0.01)) is False


def test_is_voiced_resets_silero_state_between_rolling_windows():
    pytest.importorskip("torch")
    rec = _recorder()
    vad = _FakeVad()
    rec._is_voiced(np.zeros(4800, dtype=np.float32), vad)
    assert vad.resets == 1


def test_is_voiced_falls_back_to_rms_and_warns_only_once(monkeypatch):
    import src.audio as _audio

    class _Broken:
        def __call__(self, *a, **k): raise RuntimeError("boom")

    warnings: list[str] = []
    monkeypatch.setattr(_audio._log, "warning",
                        lambda msg, *a, **k: warnings.append(str(msg)))

    rec = _recorder()
    loud = np.full(4800, 0.4, dtype=np.float32)
    assert rec._is_voiced(loud, _Broken()) is True          # RMS fallback still works
    rec._is_voiced(loud, _Broken())
    rec._is_voiced(loud, _Broken())
    # Runs every 50 ms, one warning, not a flood. Silence here is how the
    # 512-sample bug stayed invisible for so long, so the first one must fire.
    assert len(warnings) == 1
    assert "falling back to RMS" in warnings[0]


def test_start_closes_the_stream_when_start_raises(monkeypatch):
    """InputStream(...) already holds the device; if .start() fails we must
    close it. sounddevice defines no __del__, so a dropped handle leaks the
    device and its callback thread for the life of the process."""
    import sys, types
    from src.audio import AudioConfig, Recorder

    closed = []

    class _Stream:
        def __init__(self, **kw): pass
        def start(self): raise RuntimeError("device busy")
        def close(self): closed.append(True)

    fake_sd = types.ModuleType("sounddevice")
    fake_sd.InputStream = _Stream
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    rec = Recorder.__new__(Recorder)
    rec.cfg = AudioConfig()
    rec._recording = False
    rec._stream = None
    rec._vad = None
    rec._vad_warned = False
    import queue as _q
    rec._q = _q.Queue()

    with pytest.raises(RuntimeError):
        rec.start()
    assert closed == [True], "open stream leaked on the error path"
    assert rec._stream is None
    assert rec._recording is False


# ---------------------------------------------------------------------------
# main.py, stuck state machines
# ---------------------------------------------------------------------------

def test_toggle_recorder_failure_does_not_wedge_the_hotkey():
    """An unplugged mic used to kill the worker with _active still True, which
    made every later hotkey press a silent no-op until a daemon restart."""
    app = _make_app()
    app._do_dictation = MagicMock()
    app.recorder.record_until_silence.side_effect = RuntimeError("no mic")

    app.on_toggle()
    assert _wait(lambda: app._active is False), "hotkey wedged after mic failure"
    app._do_dictation.assert_not_called()

    # ...and the next press actually starts a new recording.
    app.recorder.record_until_silence.side_effect = None
    app.recorder.record_until_silence.return_value = np.zeros(16000, dtype=np.float32)
    app.on_toggle()
    assert _wait(lambda: app.recorder.record_until_silence.call_count == 2)


def test_toggle_veto_drops_the_audio_instead_of_dictating_it():
    """Ctrl+Shift held on the way to Ctrl+Shift+Win fires the veto. The toggle
    worker does not consult _active, so without the cancel flag it transcribed
    and pasted the 'aborted' audio anyway."""
    app = _make_app()
    app._do_dictation = MagicMock()

    def _record(max_seconds=120.0):
        app.on_cancel_hold()          # veto arrives mid-recording
        return np.ones(16000, dtype=np.float32)

    app.recorder.record_until_silence.side_effect = _record
    app.on_toggle()
    assert _wait(lambda: app._active is False)
    app._do_dictation.assert_not_called()


def test_toggle_cancel_flag_does_not_leak_into_the_next_recording():
    app = _make_app()
    app._do_dictation = MagicMock()

    # The worker's LAST act on the cancelled path is _tray_idle(); on_cancel_hold
    # sets the tray directly, so this is the one signal that means "the worker
    # finished". _active is not a barrier, the veto clears it early.
    finished = threading.Event()
    real_idle = app._tray_idle
    app._tray_idle = lambda: (real_idle(), finished.set())

    app.recorder.record_until_silence.side_effect = lambda max_seconds=120.0: (
        app.on_cancel_hold() or np.ones(16000, dtype=np.float32))
    app.on_toggle()
    assert finished.wait(3.0), "cancelled worker never finished"
    app._do_dictation.assert_not_called()

    app.recorder.record_until_silence.side_effect = None
    app.recorder.record_until_silence.return_value = np.ones(16000, dtype=np.float32)
    app.on_toggle()
    assert _wait(lambda: app._do_dictation.call_count == 1), "second toggle was wrongly cancelled"


@pytest.mark.parametrize("audio,label", [
    (np.zeros(0, dtype=np.float32), "no audio"),
    (np.ones(1600, dtype=np.float32) * 0.5, "too short"),      # 100 ms
    (np.full(16000, 0.0001, dtype=np.float32), "too quiet"),
])
def test_rejected_clips_return_the_tray_to_rest(audio, label):
    """Every reject gate used to return with the icon still on 'rec', a red
    mic telling the user they are being recorded when they are not."""
    app = _make_app()
    app.tray.set_state.reset_mock()
    app._do_dictation(audio)
    assert app.tray.set_state.call_args_list, f"{label}: tray never reset"
    assert app.tray.set_state.call_args_list[-1] == (("ok",),), label


def test_empty_transcript_returns_the_tray_to_rest():
    """Whisper returns "" for a cough or keyboard clatter that clears the RMS
    floor; the tray was left spinning on 'thinking'."""
    app = _make_app()
    app._pipeline_lock = threading.Lock()
    app.transcriber = MagicMock()
    app.transcriber.transcribe.return_value = ("   ", "en", {})
    app._calibration = None

    app._do_dictation(np.full(16000, 0.5, dtype=np.float32))
    assert app.tray.set_state.call_args_list[-1] == (("ok",),)


def test_tray_idle_respects_paused():
    app = _make_app(paused=True)
    app._tray_idle()
    assert app.tray.set_state.call_args_list[-1] == (("paused",),)


def test_no_caller_uses_the_bogus_idle_tray_state():
    """set_state accepts ok | paused | rec | thinking. 'idle' only rendered
    green by falling through the palette default."""
    src = Path(__file__).resolve().parent.parent / "src" / "main.py"
    assert 'set_state("idle")' not in src.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# cleanup.py, the comma-storm heuristic and repeat collapsing
# ---------------------------------------------------------------------------

def test_polish_text_keeps_a_real_comma_list():
    from src.cleanup import _polish_text
    s = "Add salt, pepper, cumin, paprika, oregano."
    assert _polish_text(s) == s


def test_polish_text_keeps_a_lowercase_list_past_the_comma_threshold():
    from src.cleanup import _polish_text
    s = "We need bread, milk, eggs, cheese, butter, jam."
    assert _polish_text(s).count(",") == 5


def test_polish_text_still_flattens_a_real_comma_storm():
    from src.cleanup import _polish_text
    out = _polish_text("What, can, be, Improving, This, System?")
    assert "," not in out


def test_comma_storm_flattening_keeps_allowlisted_proper_nouns():
    """The re-lowercase pass ignored the protected set, so a storm over real
    names came out as 'sarah michael daniel'."""
    from src.cleanup import _polish_text
    from src.casing_allowlist import PROPER_NOUNS
    out = _polish_text("Call Sarah, Michael, Daniel, Emily, Sarah.", PROPER_NOUNS)
    assert "Sarah" in out and "Michael" in out and "Daniel" in out


@pytest.mark.parametrize("text", [
    "I don't know. I don't know what to do.",
    "We should ship it. We should ship it today.",
    "Thank you. Thank you very much.",
])
def test_collapse_repeats_does_not_cross_a_sentence_boundary(text):
    """A phrase repeated at the start of the NEXT sentence is deliberate;
    deleting it left an orphaned fragment ('I don't know. What to do.')."""
    from src.cleanup import _collapse_repeats
    assert _collapse_repeats(text) == text


@pytest.mark.parametrize("text,expected", [
    ("Open Browser Open Browser", "Open Browser"),
    ("Open the browser. Open the browser.", "Open the browser."),
    ("Not Opening In Chrome Not Opening In Chrome", "Not Opening In Chrome"),
])
def test_collapse_repeats_still_kills_whisper_stutters(text, expected):
    from src.cleanup import _collapse_repeats
    assert _collapse_repeats(text) == expected


def test_normalize_dashes_does_not_swallow_a_paragraph_break():
    """`(?m)\\s*,\\s*$` crossed the newline and merged the paragraphs, right
    before the guard that exists to reject exactly that."""
    from src.cleanup import Cleaner
    assert Cleaner._normalize_dashes("one,\n\ntwo") == "one\n\ntwo"


def test_normalize_dashes_still_strips_a_trailing_comma():
    from src.cleanup import Cleaner
    assert Cleaner._normalize_dashes("one, \ntwo") == "one\ntwo"


def test_polish_text_leaves_abbreviations_and_urls_alone():
    from src.cleanup import _polish_text
    out = _polish_text("See i.e. the docs and check bit.ly/i now.")
    assert "i.e." in out and "bit.ly/i" in out


def test_polish_text_still_capitalizes_a_standalone_i():
    from src.cleanup import _polish_text
    assert _polish_text("i think i am here") == "I think I am here."


# ---------------------------------------------------------------------------
# deadweight.py, the delete-first pass flattened lists
# ---------------------------------------------------------------------------

def test_trim_preserves_single_newlines_in_a_list():
    from src.deadweight import trim
    text = "- First item.\n- Second item.\n- Third item."
    out, cuts = trim(text)
    assert out == text
    assert cuts == []


def test_trim_still_preserves_blank_line_separators():
    from src.deadweight import trim
    text = "First para sentence one. Second sentence here.\n\nSecond para."
    out, _ = trim(text)
    assert "\n\n" in out


def test_trim_still_cuts_a_dead_opener_from_a_multiline_block():
    """Line breaks are preserved, but the dead-sentence analysis still runs
    across the whole block rather than per line."""
    from src.deadweight import trim
    text = ("Machine learning has fundamentally transformed the landscape. "
            "Deep models beat classical features.\n"
            "Class imbalance remains hard.")
    out, cuts = trim(text)
    assert any("transformed the landscape" in c for c in cuts), "opener not cut"
    assert "transformed the landscape" not in out
    assert "Deep models beat classical features." in out
    assert "Class imbalance remains hard." in out
    assert "\n" in out, "line structure lost"


# ---------------------------------------------------------------------------
# learn.py, learned casings expired at the wrong threshold
# ---------------------------------------------------------------------------

def test_learned_casing_survives_a_half_life(tmp_path):
    """decay_stale deletes below 0.25 but the read path filtered at >= 1, so a
    casing taught once went dark after one 14-day half-life while still stored, and the word then lost its protection from the de-Title-Case pass."""
    from src.learn import PatternMiner
    db = tmp_path / "h.db"
    miner = PatternMiner(str(db))
    miner.add_casing("TikTok")
    assert miner.canonical_casings().get("tiktok") == "TikTok"

    # Age the entry by one half-life, then run the nightly decay.
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE casing_canon SET updated_at = updated_at - ?", (15 * 86400,))
        conn.commit()
    miner.decay_stale(half_life_days=14)

    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT count FROM casing_canon WHERE word_lc='tiktok'").fetchone()
    assert row is not None and 0.25 <= row[0] < 1, "fixture no longer exercises the gap"
    assert miner.canonical_casings().get("tiktok") == "TikTok"


# ---------------------------------------------------------------------------
# notes.py, truncated auto-titles could never match
# ---------------------------------------------------------------------------

def test_backlinks_work_for_a_truncated_auto_title(tmp_path):
    """_auto_title appends '…' on truncation; that character is in no source
    text, so both the LIKE and the \\b regex could never match."""
    from src.history import History
    from src.notes import promote_to_note, backlinks_for, _auto_title

    body = ("Rebuilding the deployment orchestration pipeline configuration "
            "for the staging cluster today")
    title = _auto_title(body)
    assert title.endswith("…"), "fixture no longer produces a truncated title"

    def _seed(h, text):
        cur = h.conn.execute(
            "INSERT INTO dictations(ts, raw_text, cleaned_text) VALUES (1, ?, ?)",
            (text, text))
        h.conn.commit()
        return cur.lastrowid

    h = History(str(tmp_path / "h.db"))
    src_id = _seed(h, body)
    nid = promote_to_note(h, src_id)          # auto-titles, so the title truncates
    other = _seed(h, f"About {body}, we shipped it.")
    _seed(h, "totally unrelated content here.")

    ids = {b[0] for b in backlinks_for(h, retriever=None, note_id=nid)}
    assert other in ids, "truncated auto-title found no lexical backlinks"
    assert src_id not in ids


# ---------------------------------------------------------------------------
# editor / retrieval / dashboard wiring
# ---------------------------------------------------------------------------

def test_review_queue_forwards_the_no_learn_casing_flag(monkeypatch):
    """`cleanup.casing.learn_from_edits: false` was honored by Edit Last but
    not by the review queue, which mined casings anyway."""
    from src import editor
    seen = {}
    monkeypatch.setattr(editor, "open_editor",
                        lambda db, rid, learn_casing=True: seen.update(learn_casing=learn_casing))
    import inspect
    sig = inspect.signature(editor.open_review_queue)
    assert "learn_casing" in sig.parameters
    # ...and the CLI passes it through.
    cli_src = (Path(__file__).resolve().parent.parent / "src" / "editor_cli.py").read_text(encoding="utf-8")
    assert "open_review_queue(db, learn_casing=learn_casing)" in cli_src
    main_src = (Path(__file__).resolve().parent.parent / "src" / "main.py").read_text(encoding="utf-8")
    queue_block = main_src.split("def tray_open_review_queue")[1].split("def ")[0]
    assert "--no-learn-casing" in queue_block


def test_mobile_trust_for_rag_is_actually_wired():
    """A documented, shipped config knob that nothing read."""
    main_src = (Path(__file__).resolve().parent.parent / "src" / "main.py").read_text(encoding="utf-8")
    assert "trust_for_rag" in main_src, "mobile.trust_for_rag is still a dead knob"


# ---------------------------------------------------------------------------
# the shipped factory default
# ---------------------------------------------------------------------------

def _repo() -> Path:
    return Path(__file__).resolve().parent.parent


def _shipped_cfg() -> Path:
    """The config every install actually starts from. The working config.yaml
    beside it is gitignored: absent in CI, personal everywhere else."""
    return _repo() / "packaging" / "default" / "config.yaml"


def test_factory_default_is_in_sync_with_config_yaml():
    """packaging/default/config.yaml is generated FROM config.yaml so a newly
    added setting is inherited automatically. If this fails, run
    `python scripts/make_default_config.py`."""
    import subprocess
    import sys as _sys
    r = subprocess.run(
        [_sys.executable, "scripts/make_default_config.py", "--check"],
        cwd=str(_repo()), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")


def test_factory_default_is_local_only_and_opt_in():
    """This file is the only config in git, and every install starts from it:
    bundled into the builds, and copied on a source checkout's first run too.
    config.yaml beside it is the maintainer's working config and is gitignored.

    It used to be the other way round, and the working config shipped
    provider=groq + allow_cloud_cleanup=true, so every dictation of every new
    user went to a cloud API while the README said local-by-default; Action
    Mode was live; and command_prefix was a word no doc mentions."""
    import yaml
    cfg = yaml.safe_load((_repo() / "packaging" / "default" / "config.yaml")
                         .read_text(encoding="utf-8"))
    assert cfg["cleanup"]["provider"] == "ollama"
    assert cfg["cleanup"]["allow_cloud_cleanup"] is False
    assert cfg["cleanup"]["verify"]["escalate_cloud"] is False
    assert cfg["cleanup"]["learning"]["teacher_enabled"] is False
    assert cfg["experimental"]["humanize_use_cloud"] is False
    assert cfg["experimental"]["humanize"] is False
    exp = cfg["experimental"]
    assert exp["command_mode"] is False and exp["action_mode"] is False
    assert exp["press_enter_command"] is False
    assert exp["action_require_prefix"] is True
    assert exp["command_prefix"] == "computer", "every doc example says 'computer, ...'"
    assert exp["humanize_text_model"] == "", "must fall back to the model the installer pulls"
    assert cfg["dashboard"]["onboarded"] is False, "a new install should get the tour"
    assert cfg["mobile"]["bind_address"] == "127.0.0.1"
    assert cfg["mobile"]["advertise_mdns"] is False
    assert not (cfg["dashboard"].get("accent_color") or "")


def test_factory_default_carries_no_secrets():
    """It ships to every user, so nothing machine-specific may ride along."""
    import re
    import yaml
    text = (_repo() / "packaging" / "default" / "config.yaml").read_text(encoding="utf-8")
    cfg = yaml.safe_load(text)
    assert not (cfg["mobile"].get("shared_key") or ""), "bridge key must be generated per install"
    # No inline credential-looking literals (keys are read from the environment).
    for pat in (r"sk-[A-Za-z0-9]{8,}", r"gsk_[A-Za-z0-9]{8,}",
                r"xox[baprs]-[A-Za-z0-9-]{8,}", r"ghp_[A-Za-z0-9]{8,}"):
        assert not re.search(pat, text), f"possible secret matching {pat}"


def test_installers_bundle_the_factory_default_not_the_working_config():
    daemon = (_repo() / "EchoFlow-Daemon.spec").read_text(encoding="utf-8")
    shell = (_repo() / "EchoFlow.spec").read_text(encoding="utf-8")
    nuitka = (_repo() / "build_nuitka.ps1").read_text(encoding="utf-8")
    assert '("config.yaml", ".")' not in daemon
    assert '("packaging/default/config.yaml", ".")' in daemon
    assert "REPO / 'config.yaml'" not in shell
    assert "'packaging' / 'default' / 'config.yaml'" in shell
    assert "--include-data-files=config.yaml=config.yaml" not in nuitka
    assert "packaging/default/config.yaml=config.yaml" in nuitka


# ---------------------------------------------------------------------------
# wave 2: privacy / security surface
# ---------------------------------------------------------------------------

def test_bridge_defaults_bind_to_loopback_with_mdns_off():
    """The code defaulted to 0.0.0.0 + mDNS-on while config.yaml ships
    loopback/off and dashboard/privacy.py REPORTS loopback for a missing key.
    A config without those keys listened on every interface and advertised
    itself, while the privacy page said it was loopback-only."""
    src = (Path(__file__).resolve().parent.parent / "src" / "main.py").read_text(encoding="utf-8")
    assert 'mobile_cfg.get("bind_address", "0.0.0.0")' not in src
    assert 'mobile_cfg.get("bind_address", "127.0.0.1")' in src
    assert 'mobile_cfg.get("advertise_mdns", True)' not in src
    assert 'mobile_cfg.get("advertise_mdns", False)' in src


def test_bridge_default_matches_the_privacy_ledger_default():
    """Whatever the default is, these two must not disagree."""
    from src.dashboard.privacy import bridge_state
    state = bridge_state({"mobile": {"enabled": True, "port": 8765}})
    assert state["bind_address"] == "127.0.0.1"
    assert state["state"] == "loopback"


@pytest.mark.parametrize("kind,slot,secret", [
    ("open_app", "app", "my divorce lawyer notes"),
    ("open_folder", "folder", "my 2026 tax audit"),
])
def test_unconfigured_action_message_does_not_echo_the_spoken_slot(kind, slot, secret):
    """classify's "^open (.+)$" catch-all puts the whole utterance in the slot.
    The failure string is persisted to voice_actions.error and to the
    notifications table, and rendered on /actions and /notifications, so
    echoing it there undid redact_args/redact_label entirely."""
    from src import voice_actions as va
    ctx = va.ActionContext(focused_title=None, focused_path=None,
                           cfg={"experimental": {}}, notify=lambda *a, **k: None)
    ok, msg = va.dispatch(va.ActionMatch(kind, "Open something", {slot: secret}), ctx)
    assert ok is False
    assert secret not in msg
    assert "configured" in msg.lower()


def test_mobile_submissions_do_not_feed_desktop_learned_patterns():
    """learned_patterns has no source filter at read time and rewrites DESKTOP
    dictations, so the bridge must honor cleanup.learning.trust_mobile before
    recording a phone correction into it."""
    src = (Path(__file__).resolve().parent.parent / "src" / "bridge.py").read_text(encoding="utf-8")
    block = src.split("source=\"mobile\",")[1][:1600]
    assert "trust_mobile" in block, "bridge records mobile pairs unconditionally"
    assert "if (trust_mobile and getattr(app_ref, \"pattern_miner\"" in src
    assert "if trust_mobile and callable(_spawn_teacher)" in src


def test_light_theme_keeps_its_own_accent_by_default():
    """base.html applies the accent override to BOTH themes, so shipping the
    dark accent in config.yaml put #3eaf6f behind white button labels on the
    light theme: 2.78:1, under the 4.5:1 AA floor and under even the 3:1
    large-text floor. Empty means "each theme uses its designed accent"."""
    import yaml
    root = Path(__file__).resolve().parent.parent
    cfg = yaml.safe_load(_shipped_cfg().read_text(encoding="utf-8"))
    assert not (cfg["dashboard"].get("accent_color") or ""), \
        "a shipped accent overrides BOTH themes; leave it empty"
    # ...and the key still EXISTS, so Settings -> General can save it.
    assert "accent_color" in cfg["dashboard"]


def test_settings_general_save_is_a_noop_when_the_colour_is_untouched(tmp_path):
    """The colour input always submits, so the rendered default and the
    save-comparison default must agree or every save rewrites the accent."""
    import shutil
    import yaml
    from src.dashboard.settings_routes import _theme_accent

    root = Path(__file__).resolve().parent.parent
    cfg = yaml.safe_load(_shipped_cfg().read_text(encoding="utf-8"))
    dc = cfg["dashboard"]
    rendered = dc.get("accent_color") or _theme_accent(dc)
    compared = str(dc.get("accent_color") or "") or _theme_accent(dc)
    assert rendered.lower() == compared.lower()


def test_humanize_shadow_stats_can_report_less_than_everything(tmp_path):
    """`changed` used to be counted over a table that only ever received
    changed rows, so the dashboard read "N of N would have changed" forever,
    for every user, regardless of how humanize was performing. That is the one
    precision signal the page tells you to review before enabling it."""
    from src.history import History
    h = History(str(tmp_path / "h.db"))

    h.log_humanize_shadow(cleaned_text="before one", humanized_text="after one",
                          style="polished", similarity=0.9, outcome="changed")
    h.log_humanize_shadow(cleaned_text="", humanized_text="", style="polished",
                          outcome="rejected")
    h.log_humanize_shadow(cleaned_text="", humanized_text="", style="polished",
                          outcome="unchanged")

    stats = h.humanize_shadow_stats(days=30)
    assert stats["n"] == 3, "rejected/unchanged evaluations must count in the denominator"
    assert stats["changed"] == 1
    assert stats["avg_similarity"] == 0.9

    # The review list shows only rows that actually carry a rewrite.
    recent = h.recent_humanize_shadow(limit=50)
    assert len(recent) == 1
    assert recent[0]["humanized_text"] == "after one"


def test_humanize_shadow_stats_still_reads_legacy_rows(tmp_path):
    """Rows written before the `outcome` column exist only when a rewrite was
    accepted, so they must still be counted as changed."""
    from src.history import History
    h = History(str(tmp_path / "h.db"))
    h.conn.execute(
        "INSERT INTO humanize_shadow(ts, dictation_id, style, cleaned_text, "
        "humanized_text, similarity, reviewed) VALUES (?,?,?,?,?,?,0)",
        (time.time(), None, "polished", "before", "after", 0.8))
    h.conn.commit()
    stats = h.humanize_shadow_stats(days=30)
    assert stats == {"days": 30, "n": 1, "changed": 1, "avg_similarity": 0.8}


def test_dictionary_terms_past_the_whisper_prompt_cap_still_count_as_known():
    """_build_custom_vocabulary truncates to 80 because that is the Whisper
    initial_prompt budget. Reusing it as the "already known" set meant a user
    with more than ~80 dictionary entries kept being re-suggested terms they
    had already promoted."""
    from src.main import App
    from src import vocab_suggest

    app = App.__new__(App)
    app.cfg = {"custom_vocabulary": [f"Term{i:03d}" for i in range(120)],
               "cleanup": {"snippets": {}}}
    app.history = None
    app.learner = None

    capped = app._build_custom_vocabulary()
    uncapped = app._build_custom_vocabulary(limit=None)
    assert len(capped) == 80, "the Whisper prompt budget still applies by default"
    assert len(uncapped) == 120

    # A term the user promoted 100th is still in their dictionary.
    late = "Term099"
    assert late not in capped and late in uncapped

    low_conf = [(late, 0.2)]
    text = f"I was talking about {late} today."
    assert vocab_suggest.filter_candidates(low_conf, text, set(uncapped)) == []
    assert vocab_suggest.filter_candidates(low_conf, text, set(capped)), \
        "fixture no longer demonstrates the bug"


def test_inbox_edit_of_a_missing_row_does_not_claim_success(tmp_path):
    """The UPDATE affected zero rows and the handler still redirected to the
    saved-dictation anchor as if it had written something."""
    import shutil
    import yaml
    from src.history import History
    from src.dashboard.app import make_app

    class _App:
        def __init__(self, cfg, cfg_path, history):
            self.cfg, self.cfg_path, self.history = cfg, cfg_path, history

    root = Path(__file__).resolve().parent.parent
    cfg_path = tmp_path / "config.yaml"
    shutil.copy(_shipped_cfg(), cfg_path)
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg["dashboard"]["onboarded"] = True
    h = History(str(tmp_path / "h.db"))
    client = make_app(_App(cfg, cfg_path, h)).test_client()

    resp = client.post("/inbox/999/edit", data={"cleaned_text": "nope"},
                       headers={"Host": "127.0.0.1:8766"})
    assert resp.status_code == 302
    assert "/#d-999" not in resp.headers["Location"]
    assert "not+found" in resp.headers["Location"] or "not%20found" in resp.headers["Location"]


def test_mobile_source_filter_is_mobile_only(tmp_path):
    """?source=mobile was byte-identical to ?source=all: _source_clause took a
    boolean, which cannot express "mobile only"."""
    from src.dashboard.analytics import _source_clause, total_words
    from src.history import History

    assert _source_clause("mobile") == " AND source = 'mobile'"
    assert _source_clause("desktop") == " AND source = 'desktop'"
    assert _source_clause("all") == ""
    # Historical boolean callers keep working.
    assert _source_clause(False) == " AND source = 'desktop'"
    assert _source_clause(True) == ""

    h = History(str(tmp_path / "h.db"))
    for text, src in [("one two three", "desktop"), ("four five", "mobile")]:
        h.conn.execute(
            "INSERT INTO dictations(ts, raw_text, cleaned_text, source) VALUES (1,?,?,?)",
            (text, text, src))
    h.conn.commit()

    assert total_words(h.conn, include_mobile="desktop") == 3
    assert total_words(h.conn, include_mobile="mobile") == 2
    assert total_words(h.conn, include_mobile="all") == 5


def test_set_scalar_does_not_resolve_a_shallow_key_to_a_nested_one(tmp_path):
    """`matched_indents[depth - 1]` read one level too shallow, so a leaf in a
    nested sub-block could satisfy a shallower path, silently rewriting a
    different setting than the one the user changed."""
    import yaml
    from src.dashboard.config_writer import set_scalar
    p = tmp_path / "c.yaml"
    p.write_text("cleanup:\n  learning:\n    enabled: true\n  enabled: false\n", encoding="utf-8")

    set_scalar(p, "cleanup.enabled", True)
    got = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert got["cleanup"]["enabled"] is True
    assert got["cleanup"]["learning"]["enabled"] is True, "wrote the wrong key"

    set_scalar(p, "cleanup.learning.enabled", False)
    got = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert got["cleanup"]["learning"]["enabled"] is False
    assert got["cleanup"]["enabled"] is True


def test_set_scalar_is_not_confused_by_blank_lines_inside_a_block(tmp_path):
    """A blank line lstrips to "\\n", not "", it used to register as an
    indent-0 entry that closed every open block."""
    import yaml
    from src.dashboard.config_writer import set_scalar
    p = tmp_path / "c.yaml"
    p.write_text("cleanup:\n  enabled: true\n\n  # a comment\n\n  snippets:\n    btw: \"x\"\n",
                 encoding="utf-8")
    set_scalar(p, "cleanup.snippets.btw", "by the way")
    got = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert got["cleanup"]["snippets"]["btw"] == "by the way"


def test_every_scalar_in_the_shipped_config_resolves_to_itself(tmp_path):
    """End-to-end guard on the resolver: writing any key must change that key
    and nothing else."""
    import shutil
    import yaml
    from src.dashboard.config_writer import set_scalar
    root = Path(__file__).resolve().parent.parent

    def leaves(node, pre=()):
        for k, v in (node or {}).items():
            if isinstance(v, dict):
                yield from leaves(v, pre + (k,))
            else:
                yield pre + (k,), v

    orig = dict(leaves(yaml.safe_load(_shipped_cfg().read_text(encoding="utf-8"))))
    scalars = {p: v for p, v in orig.items()
               if isinstance(v, (str, int, float, bool)) or v is None}
    assert len(scalars) > 100, "fixture no longer covers the real config"

    problems = []
    for path, val in scalars.items():
        dst = tmp_path / f"{'_'.join(path)}.yaml"
        shutil.copy(_shipped_cfg(), dst)
        new = (not val) if isinstance(val, bool) else (
            "ZZSENT" if (isinstance(val, str) or val is None) else val + 7)
        set_scalar(dst, ".".join(path), new)
        after = dict(leaves(yaml.safe_load(dst.read_text(encoding="utf-8"))))
        changed = [".".join(p) for p in orig if after.get(p) != orig[p]]
        if changed != [".".join(path)]:
            problems.append((".".join(path), changed))
    assert not problems, f"keys resolved to the wrong line: {problems[:5]}"


def test_config_yaml_defines_every_dashboard_key_the_settings_form_writes(tmp_path):
    """config_writer is scalar-only and refuses to create keys, so a settings
    field with no key in config.yaml fails the whole save, and, because the
    error path precedes the hot-reload, silently skips applying the language."""
    import shutil
    from src.dashboard.config_writer import set_scalar
    root = Path(__file__).resolve().parent.parent
    dst = tmp_path / "config.yaml"
    shutil.copy(_shipped_cfg(), dst)
    set_scalar(dst, "dashboard.accent_color", "#123456")
