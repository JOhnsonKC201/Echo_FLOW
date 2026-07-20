"""Voice-samples CRUD + voice-profile assembly tests ("My Voice" feature)."""
from __future__ import annotations

import time

import pytest

from src.history import History
from src.dashboard import voice_samples as vs
from src import voice_profile as vp


def _h(tmp_path):
    return History(str(tmp_path / "h.db"))


def _add_dictation(h, *, cleaned, original=None, rating=None, source="desktop",
                   ts=None):
    h.conn.execute(
        "INSERT INTO dictations(ts, style, raw_text, cleaned_text, "
        "original_cleaned, user_rating, source) VALUES (?,?,?,?,?,?,?)",
        (ts if ts is not None else time.time(), "polished",
         "raw " + cleaned, cleaned, original, rating, source),
    )
    h.conn.commit()


def test_add_and_list_newest_first(tmp_path):
    h = _h(tmp_path)
    a = vs.add_sample(h.conn, "first paragraph in my voice")
    b = vs.add_sample(h.conn, "second, written later")
    rows = vs.list_samples(h.conn)
    assert len(rows) == 2
    assert [r["id"] for r in rows] == [b, a]           # newest first
    assert rows[0]["enabled"] is True
    assert rows[0]["char_len"] == len("second, written later")
    assert rows[0]["source"] == "pasted"


def test_add_validates(tmp_path):
    h = _h(tmp_path)
    with pytest.raises(ValueError):
        vs.add_sample(h.conn, "   ")
    with pytest.raises(ValueError):
        vs.add_sample(h.conn, "x" * (vs.MAX_SAMPLE_CHARS + 1))


def test_update_and_delete(tmp_path):
    h = _h(tmp_path)
    sid = vs.add_sample(h.conn, "draft")
    assert vs.update_sample(h.conn, sid, "the revised sample text")
    assert vs.list_samples(h.conn)[0]["content"] == "the revised sample text"
    assert vs.delete_sample(h.conn, sid)
    assert vs.list_samples(h.conn) == []
    assert vs.delete_sample(h.conn, sid) is False       # gone → no-op


def test_enabled_texts_respects_flag(tmp_path):
    h = _h(tmp_path)
    keep = vs.add_sample(h.conn, "kept sample")
    drop = vs.add_sample(h.conn, "disabled sample")
    assert set(vs.enabled_texts(h.conn)) == {"kept sample", "disabled sample"}
    assert vs.set_enabled(h.conn, drop, False)
    assert vs.enabled_texts(h.conn) == ["kept sample"]
    # a disabled row still lists, just isn't fed to the profile
    assert any(not r["enabled"] for r in vs.list_samples(h.conn))
    assert keep in [r["id"] for r in vs.list_samples(h.conn)]


def test_bulk_import_splits_on_blank_lines(tmp_path):
    h = _h(tmp_path)
    raw = "para one\nstill para one\n\npara two\n\n\n   \n\npara three"
    res = vs.bulk_import(h.conn, raw)
    assert res["added"] == 3
    assert res["invalid"] == 0
    texts = vs.enabled_texts(h.conn)
    assert "para one\nstill para one" in texts
    assert "para two" in texts
    assert "para three" in texts


def test_bulk_import_skips_oversize_block(tmp_path):
    h = _h(tmp_path)
    big = "x" * (vs.MAX_SAMPLE_CHARS + 5)
    res = vs.bulk_import(h.conn, f"ok block\n\n{big}")
    assert res["added"] == 1
    assert res["invalid"] == 1


# --- voice_profile.build -----------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_profile_cache():
    vp.invalidate()
    yield
    vp.invalidate()


def test_profile_empty_when_no_data(tmp_path):
    h = _h(tmp_path)
    assert vp.build(h) == ""


def test_profile_includes_samples_and_exemplars(tmp_path):
    h = _h(tmp_path)
    vs.add_sample(h.conn, "This is how I actually write, plainly and directly.")
    _add_dictation(h, cleaned="Approved line I gave a thumbs up.", rating=1)
    _add_dictation(h, cleaned="Corrected line the way I wanted it.",
                   original="the model's original wording")
    prof = vp.build(h)
    assert "WRITING SAMPLES" in prof
    assert "how I actually write" in prof
    assert "DICTATIONS YOU KEPT OR CORRECTED" in prof
    assert "Approved line" in prof
    assert "Corrected line" in prof


def test_profile_excludes_passive_and_mobile(tmp_path):
    h = _h(tmp_path)
    # passively accepted (no rating, original == cleaned) → not a voice signal
    _add_dictation(h, cleaned="Passive accepted line untouched.", original="Passive accepted line untouched.")
    # approved but from mobile → excluded
    _add_dictation(h, cleaned="Mobile approved line.", rating=1, source="mobile")
    prof = vp.build(h)
    assert prof == ""       # nothing eligible → empty → humanize skipped


def test_profile_dedupes_and_caps(tmp_path):
    h = _h(tmp_path)
    for i in range(vp.MAX_EXEMPLARS + 4):
        _add_dictation(h, cleaned=f"Unique corrected exemplar number {i}.",
                       original="x", ts=1000 + i)
    _add_dictation(h, cleaned="Dupe exemplar kept once.", rating=1, ts=2000)
    _add_dictation(h, cleaned="Dupe exemplar kept once.", rating=1, ts=2001)
    prof = vp.build(h)
    assert prof.count("Dupe exemplar kept once.") == 1
    exemplar_lines = [ln for ln in prof.splitlines() if ln.startswith("- ")]
    assert len(exemplar_lines) <= vp.MAX_EXEMPLARS


@pytest.mark.parametrize("value,expected", [
    (False, "off"), (True, "on"), ("shadow", "shadow"),
    ("on", "on"), ("true", "on"), ("off", "off"), ("nonsense", "off"),
    (None, "off"),
])
def test_humanize_mode_normalizer(value, expected):
    assert vp.humanize_mode_for_cfg({"humanize": value}) == expected


def test_humanize_mode_missing_key_is_off():
    assert vp.humanize_mode_for_cfg({}) == "off"


def test_profile_cache_invalidates(tmp_path):
    h = _h(tmp_path)
    vs.add_sample(h.conn, "first sample text here")
    assert "first sample" in vp.build(h)
    vs.add_sample(h.conn, "second sample added later")
    assert "second sample" not in vp.build(h)   # cached
    vp.invalidate()
    assert "second sample" in vp.build(h)        # fresh
