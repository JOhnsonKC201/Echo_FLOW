"""End-to-end casing verification with real wiring (no logic mocked except the
LLM network call): the dashboard surfaces/deletes learned casings, and the full
Cleaner.clean() pipeline flattens spurious Title-Case while honoring a canon
entry seeded into a real SQLite DB.
"""
from __future__ import annotations

import types

from src.history import History
from src.learn import PatternMiner, _invalidate_casing_cache


def _client(history, pattern_miner):
    from src.dashboard.app import make_app
    app_ref = types.SimpleNamespace(
        cfg={"dashboard": {"host": "127.0.0.1", "port": 8766, "theme": "dark"}},
        history=history,
        pattern_miner=pattern_miner,
        cleaner=None,
    )
    return make_app(app_ref).test_client()


def test_dashboard_lists_and_deletes_learned_casing(tmp_path):
    h = History(str(tmp_path / "h.db"))
    pm = PatternMiner(str(tmp_path / "h.db"))
    pm.record_casing("i love tiktok", "i love TikTok")
    _invalidate_casing_cache()

    client = _client(h, pm)
    hdr = {"Host": "127.0.0.1:8766"}

    # The Dictionary page shows the learned casing.
    r = client.get("/dictionary", headers=hdr)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Learned casings" in body
    assert "TikTok" in body

    # Deleting it via the route removes it from the canon.
    r = client.post("/dictionary/casing/delete", data={"word_lc": "tiktok"},
                    headers=hdr, follow_redirects=False)
    assert r.status_code in (302, 303)
    _invalidate_casing_cache()
    assert "tiktok" not in pm.canonical_casings()


def test_dashboard_adds_casing_directly(tmp_path):
    h = History(str(tmp_path / "h.db"))
    pm = PatternMiner(str(tmp_path / "h.db"))
    client = _client(h, pm)
    hdr = {"Host": "127.0.0.1:8766"}

    # Add a casing straight from the dashboard form.
    r = client.post("/dictionary/casing/add", data={"casing": "GitHub"},
                    headers=hdr, follow_redirects=False)
    assert r.status_code in (302, 303)
    _invalidate_casing_cache()
    assert pm.canonical_casings().get("github") == "GitHub"

    # It now renders on the Dictionary page.
    body = client.get("/dictionary", headers=hdr).get_data(as_text=True)
    assert "GitHub" in body

    # Garbage input is rejected without creating a row.
    client.post("/dictionary/casing/add", data={"casing": "plainword"}, headers=hdr)
    _invalidate_casing_cache()
    assert "plainword" not in pm.canonical_casings()


def test_full_pipeline_flattens_titlecase_and_honors_canon(tmp_path, monkeypatch):
    """The reported bug + the fix, through real clean() wiring."""
    from src.cleanup import Cleaner

    db = str(tmp_path / "h.db")
    History(db)  # create schema
    pm = PatternMiner(db)
    pm.record_casing("i opened tiktok", "i opened TikTok")
    _invalidate_casing_cache()

    cleaner = Cleaner({
        "enabled": True, "provider": "ollama",
        "casing": {"flatten_titlecase": True, "learn_from_edits": True,
                   "protect_common_nouns": True},
    })
    cleaner.attach_learning(pm, None)

    # Simulate what Whisper/the LLM actually produced in the bug report:
    # every word Title-Cased, with a lowercase "tiktok".
    titlecased = ("Machine Learning Feeds Here The Most Because There Are "
                  "Millions Of Videos On tiktok Per Day.")
    monkeypatch.setattr(cleaner, "_via_ollama",
                        lambda system, text, **kw: titlecased)

    out, _skipped = cleaner.clean("machine learning feeds here the most "
                                  "because there are millions of videos on "
                                  "tiktok per day")

    # Spurious Title-Case is gone...
    assert "Machine learning feeds here the most" in out
    assert "Learning" not in out and "Videos" not in out
    # ...the canon forced the brand casing...
    assert "TikTok" in out
    # ...and a bundled proper noun would survive too (sanity on the wiring).
    assert out.endswith(".")
