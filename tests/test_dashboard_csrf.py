"""Cross-site request forgery guard for the dashboard.

The Host-header allowlist stops DNS rebinding, but not a plain cross-site form
POST: the victim's browser sends the real `Host: 127.0.0.1:8766`, so any page
the user visits could auto-submit to /privacy/wipe. Browsers attach `Origin`
and `Sec-Fetch-Site` to those requests and a page cannot forge them, so the
guard keys on those.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from src.history import History

REPO_CFG = (Path(__file__).resolve().parent.parent
            / "packaging" / "default" / "config.yaml")
HOST = {"Host": "127.0.0.1:8766"}
WIPE = {"confirm": "WIPE"}


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
    h.log(window_title="t", style="default", language="en",
          duration_ms=1, raw_text="x", cleaned_text="x")
    app_ref = _App(cfg, cfg_path, h)
    from src.dashboard.app import make_app
    return make_app(app_ref).test_client(), app_ref


def _rows(app_ref) -> int:
    return app_ref.history.conn.execute(
        "SELECT COUNT(*) FROM dictations").fetchone()[0]


# --- Attacks that must be blocked ------------------------------------------

def test_cross_site_origin_cannot_wipe_history(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/privacy/wipe", data=WIPE,
                    headers={**HOST, "Origin": "https://evil.example"})
    assert r.status_code == 403
    assert _rows(app_ref) == 1


def test_sec_fetch_site_cross_site_is_blocked_without_origin(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/privacy/wipe", data=WIPE,
                    headers={**HOST, "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert _rows(app_ref) == 1


def test_opaque_null_origin_is_blocked(tmp_path):
    # Sandboxed iframes and file:// pages send `Origin: null`.
    client, app_ref = _client(tmp_path)
    r = client.post("/privacy/wipe", data=WIPE,
                    headers={**HOST, "Origin": "null"})
    assert r.status_code == 403
    assert _rows(app_ref) == 1


def test_other_localhost_port_is_cross_origin(tmp_path):
    # Another local dev server (say :3000) is a different origin.
    client, app_ref = _client(tmp_path)
    r = client.post("/privacy/wipe", data=WIPE,
                    headers={**HOST, "Origin": "http://127.0.0.1:3000"})
    assert r.status_code == 403
    assert _rows(app_ref) == 1


# --- Legitimate requests that must still work --------------------------------

def test_same_origin_form_post_still_wipes(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/privacy/wipe", data=WIPE,
                    headers={**HOST, "Origin": "http://127.0.0.1:8766",
                             "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 302
    assert _rows(app_ref) == 0


def test_localhost_alias_origin_is_same_origin(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/privacy/wipe", data=WIPE,
                    headers={"Host": "localhost:8766",
                             "Origin": "http://localhost:8766"})
    assert r.status_code == 302
    assert _rows(app_ref) == 0


def test_non_browser_client_without_origin_is_allowed(tmp_path):
    # curl and scripts send neither header and cannot be driven by a web page.
    client, app_ref = _client(tmp_path)
    r = client.post("/privacy/wipe", data=WIPE, headers=HOST)
    assert r.status_code == 302
    assert _rows(app_ref) == 0


def test_cross_site_get_is_not_blocked(tmp_path):
    # Reads have no side effects; following a link to the dashboard must work.
    client, _ = _client(tmp_path)
    r = client.get("/privacy", headers={**HOST, "Origin": "https://evil.example",
                                        "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 200
