"""Phase 14+ — persisted intent-model shadow measurement (MODEL-SHADOW).

The shadow mode used to be a log line; these tests pin the persisted form:
a nullable ``model_pred`` JSON column on ``voice_actions`` that records what
the model predicted, whether it agreed with the regex on real utterances, and
what it would have recovered on regex misses — so precision can be measured
online before the model is ever trusted to fire.

Privacy invariant: ``model_pred`` never contains slot text (queries, note
bodies, URLs) — only handler ids, confidences, and comparison booleans.
"""
from __future__ import annotations

import json
import sqlite3
import time

from src import intent_model as im
from src import voice_actions as va
from src.history import History


def _h(tmp_path):
    return History(str(tmp_path / "h.db"))


def _cfg(**experimental):
    return {"experimental": experimental}


# --- agreement_record: the JSON payload builder -------------------------------

def test_agreement_record_agree_with_regex_match():
    cfg = _cfg()
    res = im.infer("go to github.com", cfg)          # keyword → open_url
    assert res.match is not None
    regex_match = va.ActionMatch("open_url", "Open https://github.com",
                                 {"url": "https://github.com"})
    rec = im.agreement_record(res, "keyword", regex_match=regex_match)
    assert rec["backend"] == "keyword"
    assert rec["handler"] == "open_url"
    assert rec["gated"] is True
    assert rec["resolved"] is True
    assert rec["action"] == "open_url"
    assert rec["agree"] is True
    assert rec["args_match"] is True
    assert 0.0 < rec["conf"] <= 1.0


def test_agreement_record_disagreement():
    cfg = _cfg()
    res = im.infer("go to github.com", cfg)          # model says open_url
    regex_match = va.ActionMatch("web_search", "Search the web",
                                 {"query": "github"})
    rec = im.agreement_record(res, "keyword", regex_match=regex_match)
    assert rec["agree"] is False
    assert rec["args_match"] is False


def test_agreement_record_unresolved_prediction():
    # "launch spotify" gates but no app is configured → build_match refuses.
    cfg = _cfg(action_apps={})
    res = im.infer("launch spotify", cfg)
    assert res.gated is True and res.match is None
    rec = im.agreement_record(res, "keyword")
    assert rec["resolved"] is False
    assert "action" not in rec
    assert "agree" not in rec            # no regex match to compare against


def test_agreement_record_recovered_provenance():
    cfg = _cfg()
    res = im.infer("navigate to github.com", cfg)
    rec = im.agreement_record(res, "keyword", recovered=True)
    assert rec["recovered"] is True
    # A plain shadow record must NOT carry the recovered flag.
    assert "recovered" not in im.agreement_record(res, "keyword")


def test_agreement_record_never_leaks_slot_text():
    # The record for a search prediction must not contain the query text.
    cfg = _cfg()
    res = im.infer("google my very secret plans", cfg)
    assert res.prediction.handler == "web_search"
    rec = im.agreement_record(res, "keyword", regex_match=res.match)
    dumped = json.dumps(rec)
    assert "secret" not in dumped
    assert "plans" not in dumped


# --- history: schema, round-trip, migration -----------------------------------

def test_voice_actions_has_model_pred_column(tmp_path):
    h = _h(tmp_path)
    cols = [r[1] for r in h.conn.execute(
        "PRAGMA table_info(voice_actions)").fetchall()]
    assert "model_pred" in cols


def test_log_action_round_trips_model_pred(tmp_path):
    h = _h(tmp_path)
    rec = json.dumps({"backend": "keyword", "handler": "open_url",
                      "conf": 0.9, "gated": True, "resolved": True,
                      "action": "open_url", "agree": True, "args_match": True})
    h.log_action(body="<redacted len=16>", handler="open_url",
                 args_json='{"url": "https://github.com"}',
                 label="Open https://github.com", ok=True, model_pred=rec)
    row = h.recent_actions()[0]
    assert row["model_pred"] == rec
    # Rows logged without a prediction stay NULL, not "".
    h.log_action(body="x", handler="open_app", args_json=None)
    assert h.recent_actions()[0]["model_pred"] is None


def test_migrates_legacy_voice_actions_table(tmp_path):
    """A DB created before MODEL-SHADOW has voice_actions without model_pred;
    opening it must add the column without touching existing rows."""
    db = tmp_path / "h.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE voice_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            body TEXT NOT NULL,
            handler TEXT NOT NULL,
            args TEXT,
            label TEXT,
            ok INTEGER NOT NULL DEFAULT 1,
            error TEXT
        );
        INSERT INTO voice_actions(ts, body, handler, ok)
        VALUES (1.0, 'open notepad', 'open_app', 1);
    """)
    conn.commit(); conn.close()
    h = History(str(db))
    cols = [r[1] for r in h.conn.execute(
        "PRAGMA table_info(voice_actions)").fetchall()]
    assert cols.count("model_pred") == 1
    row = h.recent_actions()[0]
    assert row["handler"] == "open_app" and row["model_pred"] is None


def test_model_pred_migration_idempotent(tmp_path):
    db = str(tmp_path / "h.db")
    History(db)
    h = History(db)   # second open must not raise / duplicate
    cols = [r[1] for r in h.conn.execute(
        "PRAGMA table_info(voice_actions)").fetchall()]
    assert cols.count("model_pred") == 1


# --- history.intent_agreement_stats -------------------------------------------

def _insert(h, *, handler, ok=1, model_pred=None, ts=None):
    h.conn.execute(
        "INSERT INTO voice_actions(ts, body, handler, ok, model_pred) "
        "VALUES (?,?,?,?,?)",
        (ts if ts is not None else time.time(), "<redacted>", handler,
         ok, model_pred))
    h.conn.commit()


def test_agreement_stats_buckets(tmp_path):
    h = _h(tmp_path)
    # Two executed regex rows with a shadow compare: one agree, one disagree.
    _insert(h, handler="open_url", model_pred=json.dumps(
        {"backend": "keyword", "handler": "open_url", "conf": 0.9,
         "gated": True, "resolved": True, "action": "open_url",
         "agree": True, "args_match": True}))
    _insert(h, handler="web_search", model_pred=json.dumps(
        {"backend": "keyword", "handler": "open", "conf": 0.9,
         "gated": True, "resolved": True, "action": "open_url",
         "agree": False, "args_match": False}))
    # Two shadow rows (regex miss): one resolved, one refused by the allowlist.
    _insert(h, handler="intent_shadow", model_pred=json.dumps(
        {"backend": "keyword", "handler": "open_url", "conf": 0.88,
         "gated": True, "resolved": True, "action": "open_url"}))
    _insert(h, handler="intent_shadow", model_pred=json.dumps(
        {"backend": "keyword", "handler": "open", "conf": 0.9,
         "gated": True, "resolved": False}))
    # One live-recovered execution that succeeded.
    _insert(h, handler="open_url", ok=1, model_pred=json.dumps(
        {"backend": "keyword", "handler": "open_url", "conf": 0.88,
         "gated": True, "resolved": True, "action": "open_url",
         "recovered": True}))
    # Plain regex rows without model_pred don't count anywhere.
    _insert(h, handler="open_app")

    s = h.intent_agreement_stats(days=30)
    assert s["hits"] == {"n": 2, "agree": 1, "args_match": 1}
    assert s["shadow"] == {"n": 2, "resolved": 1}
    assert s["recovered"] == {"n": 1, "ok": 1}
    assert s["days"] == 30


def test_agreement_stats_skips_malformed_and_old_rows(tmp_path):
    h = _h(tmp_path)
    _insert(h, handler="intent_shadow", model_pred="{not json")
    _insert(h, handler="intent_shadow", ts=time.time() - 40 * 86400,
            model_pred=json.dumps({"backend": "keyword", "handler": "open_url",
                                   "conf": 0.9, "gated": True,
                                   "resolved": True}))
    s = h.intent_agreement_stats(days=30)
    assert s["hits"]["n"] == 0
    assert s["shadow"]["n"] == 0
    assert s["recovered"]["n"] == 0


def test_agreement_stats_empty_db(tmp_path):
    s = _h(tmp_path).intent_agreement_stats()
    assert s["hits"] == {"n": 0, "agree": 0, "args_match": 0}
    assert s["shadow"] == {"n": 0, "resolved": 0}
    assert s["recovered"] == {"n": 0, "ok": 0}


# --- config helpers -------------------------------------------------------------

def test_floor_for_cfg_keyword_default():
    assert im.floor_for_cfg({}) == im.DEFAULT_MIN_CONF
    assert im.floor_for_cfg({"action_intent_min_conf": 0.6}) == 0.6


def test_floor_for_cfg_model_backend():
    exp = {"action_intent_backend": "model"}
    assert im.floor_for_cfg(exp) == im.DEFAULT_MODEL_MIN_CONF
    exp["action_intent_model_min_conf"] = 0.5
    assert im.floor_for_cfg(exp) == 0.5


def test_backend_for_cfg_normalizes():
    assert im.backend_for_cfg({}) == "keyword"
    assert im.backend_for_cfg({"action_intent_backend": " Model "}) == "model"
    assert im.backend_for_cfg({"action_intent_backend": "bogus"}) == "keyword"


# --- warm_in_background ----------------------------------------------------------

class _FakeWarmPredictor:
    def __init__(self):
        self.warmed = 0

    def warm(self):
        self.warmed += 1

    def predict(self, body):   # predictor contract
        return im._NONE


def test_warm_in_background_warms_model_backend(monkeypatch):
    fake = _FakeWarmPredictor()
    monkeypatch.setattr("src.intent_classifier.get_model_predictor",
                        lambda path=None: fake)
    cfg = _cfg(action_intent_model="shadow", action_intent_backend="model")
    t = im.warm_in_background(cfg)
    assert t is not None
    t.join(timeout=5)
    assert fake.warmed == 1


def test_warm_in_background_noop_for_keyword_backend():
    cfg = _cfg(action_intent_model=True)   # default backend = keyword
    assert im.warm_in_background(cfg) is None


def test_warm_in_background_noop_when_disabled():
    cfg = _cfg(action_intent_backend="model")   # backend set but feature off
    assert im.warm_in_background(cfg) is None
