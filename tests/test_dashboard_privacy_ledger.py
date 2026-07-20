"""PR-E — Privacy first-class page + ledger + export zip."""
from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path

import pytest
import yaml

from src.history import History
from src.dashboard import privacy as priv


REPO_CFG = Path(__file__).resolve().parent.parent / "config.yaml"
HOST = {"Host": "127.0.0.1:8766"}


class _App:
    def __init__(self, cfg, cfg_path, history):
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.history = history


def _client(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    shutil.copy(REPO_CFG, cfg_path)
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg["dashboard"]["onboarded"] = True
    h = History(str(tmp_path / "h.db"))
    app_ref = _App(cfg, cfg_path, h)
    from src.dashboard.app import make_app
    return make_app(app_ref).test_client(), app_ref


# --- Pure helpers ----------------------------------------------------------

def test_bridge_state_disabled():
    out = priv.bridge_state({"mobile": {"enabled": False}})
    assert out["state"] == "disabled" and out["warn"] is False


def test_bridge_state_loopback():
    out = priv.bridge_state({"mobile": {"enabled": True, "bind_address": "127.0.0.1"}})
    assert out["state"] == "loopback" and out["warn"] is False


def test_bridge_state_lan_warns():
    out = priv.bridge_state({"mobile": {"enabled": True, "bind_address": "0.0.0.0"}})
    assert out["state"] == "lan" and out["warn"] is True


def test_humanize_state_local_by_default():
    assert priv.humanize_state({})["cloud"] is False
    # on but no cloud opt-in → still local, no egress flag
    st = priv.humanize_state({"experimental": {"humanize": "on"}})
    assert st["enabled"] is True and st["cloud"] is False and st["warn"] is False


def test_humanize_state_cloud_requires_both_flags():
    cfg = {"experimental": {"humanize": "on", "humanize_use_cloud": True},
           "cleanup": {"allow_cloud_cleanup": True}}
    st = priv.humanize_state(cfg)
    assert st["cloud"] is True and st["warn"] is True
    assert "Groq" in st["endpoint"]
    # missing allow_cloud_cleanup → no egress even with humanize_use_cloud
    cfg2 = {"experimental": {"humanize": "on", "humanize_use_cloud": True}}
    assert priv.humanize_state(cfg2)["cloud"] is False


def test_ledger_flags_humanize_cloud_egress(tmp_path):
    cfg = {"experimental": {"humanize": "on", "humanize_use_cloud": True},
           "cleanup": {"allow_cloud_cleanup": True}}
    out = priv.ledger(cfg, tmp_path / "m.db", tmp_path / "c.yaml", tmp_path)
    assert out["humanize"]["cloud"] is True
    assert "My Voice" in out["egress_provenance"]
    assert out["egress_30d"] == 0        # still zero measured; note explains opt-in


def test_humanize_bytes_thresholds():
    assert priv.humanize_bytes(0) == "0 B"
    assert priv.humanize_bytes(500) == "500 B"
    assert priv.humanize_bytes(2048) == "2.0 KB"
    assert priv.humanize_bytes(5 * 1024 * 1024) == "5.0 MB"


def test_egress_is_zero_by_construction(tmp_path):
    """The architectural truth: Echo Flow never leaves the box. The ledger
    reports egress_30d=0 as a fact, not a measurement — if this regresses
    something has been added to the daemon's outbound surface."""
    out = priv.ledger({}, tmp_path / "missing.db", tmp_path / "missing.yaml", tmp_path)
    assert out["egress_30d"] == 0


def test_ledger_reports_db_size(tmp_path):
    db = tmp_path / "h.db"
    db.write_bytes(b"x" * 1234)
    out = priv.ledger({}, db, tmp_path / "c.yaml", tmp_path)
    assert out["db_size_bytes"] == 1234


def test_build_export_zip_contains_config_and_db(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dashboard: { theme: dark }\n", encoding="utf-8")
    db = tmp_path / "h.db"
    db.write_bytes(b"sqlite-bytes")
    data = priv.build_export_zip(cfg, db)
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    assert "config.yaml" in names
    assert "history.db" in names
    assert "README.txt" in names
    assert zf.read("history.db") == b"sqlite-bytes"


def test_build_export_zip_handles_missing_files(tmp_path):
    """Exporting when nothing exists should still produce a valid zip with
    just the README. No crash."""
    data = priv.build_export_zip(tmp_path / "no.yaml", tmp_path / "no.db")
    zf = zipfile.ZipFile(io.BytesIO(data))
    assert "README.txt" in zf.namelist()


# --- Routes -----------------------------------------------------------------

def test_privacy_page_renders(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/privacy", headers=HOST)
    assert r.status_code == 200
    assert b"Privacy" in r.data
    assert b"Network calls leaving this machine" in r.data
    assert b"Mobile bridge" in r.data
    assert b"Ollama endpoint" in r.data
    assert b"Wipe dictation history" in r.data


def test_privacy_page_shows_egress_zero(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/privacy", headers=HOST)
    # The "0" should appear right after the egress-30d label.
    assert b'>0<' in r.data


def test_privacy_page_warns_on_lan_bridge(tmp_path):
    client, app_ref = _client(tmp_path)
    app_ref.cfg["mobile"]["enabled"] = True
    app_ref.cfg["mobile"]["bind_address"] = "0.0.0.0"
    r = client.get("/privacy", headers=HOST)
    assert b"LAN" in r.data


def test_privacy_wipe_requires_confirm(tmp_path):
    client, app_ref = _client(tmp_path)
    app_ref.history.log(window_title="t", style="default", language="en",
                        duration_ms=1, raw_text="x", cleaned_text="x")
    r = client.post("/privacy/wipe", headers=HOST, data={"confirm": "nope"})
    assert r.status_code == 302
    assert "WIPE" in r.headers["Location"]
    n = app_ref.history.conn.execute("SELECT COUNT(*) FROM dictations").fetchone()[0]
    assert n == 1


def test_privacy_wipe_deletes_on_confirm(tmp_path):
    client, app_ref = _client(tmp_path)
    app_ref.history.log(window_title="t", style="default", language="en",
                        duration_ms=1, raw_text="x", cleaned_text="x")
    r = client.post("/privacy/wipe", headers=HOST, data={"confirm": "WIPE"})
    assert r.status_code == 302
    n = app_ref.history.conn.execute("SELECT COUNT(*) FROM dictations").fetchone()[0]
    assert n == 0


def test_privacy_export_returns_zip(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/privacy/export.zip", headers=HOST)
    assert r.status_code == 200
    assert r.mimetype == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.data))
    assert "README.txt" in zf.namelist()


def test_privacy_in_sidebar(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/privacy", headers=HOST)
    assert b'href="/privacy"' in r.data
