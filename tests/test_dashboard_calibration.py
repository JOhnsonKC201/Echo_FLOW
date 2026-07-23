"""Calibration dashboard flow: start -> status -> apply (Phase 3)."""
from __future__ import annotations

from src.history import History
from src.learn import PatternMiner


class _App:
    def __init__(self, cfg, history, miner):
        self.cfg = cfg
        self.history = history
        self.pattern_miner = miner
        self._calibration = None
        self._reload_calls = 0

    def reload_config(self):
        self._reload_calls += 1


def _client(tmp_path):
    cfg = {"hotkey": {"combo": "ctrl+shift"}, "dashboard": {"theme": "dark"}}
    h = History(str(tmp_path / "h.db"))
    miner = PatternMiner(str(tmp_path / "p.db"))
    app_ref = _App(cfg, h, miner)
    from src.dashboard.app import make_app
    return make_app(app_ref).test_client(), app_ref


HOST = {"Host": "127.0.0.1:8766"}


def test_calibration_page_renders_start_state(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/calibration", headers=HOST)
    assert r.status_code == 200
    assert b"Start calibration" in r.data
    assert b"ctrl+shift" in r.data          # the real hotkey is surfaced


def test_calibration_start_creates_session(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/calibration/start", headers=HOST)
    assert r.status_code == 302
    assert app_ref._calibration is not None
    assert app_ref._calibration.active


def test_calibration_status_reports_progress(tmp_path):
    client, app_ref = _client(tmp_path)
    client.post("/calibration/start", headers=HOST)
    # Simulate the daemon recording one spoken sentence.
    app_ref._calibration.submit("some transcript")
    s = client.get("/calibration/status", headers=HOST).get_json()
    assert s["started"] is True
    assert s["recorded"] == 1
    assert s["total"] == len(app_ref._calibration.sentences)


def test_calibration_apply_seeds_and_clears(tmp_path):
    client, app_ref = _client(tmp_path)
    client.post("/calibration/start", headers=HOST)
    # Read every sentence, mangling one known term so it gets pinned.
    sess = app_ref._calibration
    for i, sent in enumerate(sess.sentences):
        heard = sent.replace("Kubernetes", "cube are nets")
        sess.submit(heard)
    assert sess.done

    r = client.post("/calibration/apply", headers=HOST)
    assert r.status_code == 302
    assert app_ref._calibration is None          # session consumed
    assert app_ref._reload_calls >= 1            # pinned terms hot-applied
    from src.dashboard import vocabulary
    terms = [t["term"] for t in vocabulary.list_terms(app_ref.history.conn)]
    assert "Kubernetes" in terms                 # ground-truth term pinned


def test_calibration_apply_without_session_is_safe(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/calibration/apply", headers=HOST)
    assert r.status_code == 302                  # no crash, just a flash
    assert app_ref._calibration is None


def test_calibration_cancel_clears_session(tmp_path):
    client, app_ref = _client(tmp_path)
    client.post("/calibration/start", headers=HOST)
    r = client.post("/calibration/cancel", headers=HOST)
    assert r.status_code == 302
    assert app_ref._calibration is None
