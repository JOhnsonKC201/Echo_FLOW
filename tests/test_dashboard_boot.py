"""Phase 0 acceptance tests for the desktop dashboard.

Verifies the shell boots, every section route returns 200, the Host: header
guard rejects rebinding attacks, and the port-scan fallback works.
"""
from __future__ import annotations

import socket
import types

import pytest


# --- App ref helpers ---------------------------------------------------------

def _fake_app_ref(host: str = "127.0.0.1", port: int = 8766):
    """Minimal duck-typed App stand-in: only `.cfg` is needed for make_app."""
    return types.SimpleNamespace(cfg={
        "dashboard": {
            "enabled": True,
            "host": host,
            "port": port,
            "theme": "dark",
        }
    })


def _flask_client(app_ref):
    from src.dashboard.app import make_app
    return make_app(app_ref).test_client()


# --- Route smoke tests -------------------------------------------------------

SECTION_PATHS = [
    "/",
    "/insights",
    "/dictionary",
    "/snippets",
    "/style",
    "/transforms",
    "/scratchpad",
    "/settings/general",
    "/notifications",
]


@pytest.mark.parametrize("path", SECTION_PATHS)
def test_section_returns_200_with_sidebar(path):
    client = _flask_client(_fake_app_ref())
    r = client.get(path, headers={"Host": "127.0.0.1:8766"})
    assert r.status_code == 200, f"{path} -> {r.status_code}"
    body = r.get_data(as_text=True)
    # Every page extends base.html which includes the sidebar nav.
    assert "Echo Flow" in body
    assert "nav-item" in body
    assert "Home" in body and "Insights" in body and "Settings" in body


def test_healthz_returns_ok():
    client = _flask_client(_fake_app_ref())
    r = client.get("/api/healthz", headers={"Host": "127.0.0.1:8766"})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}


# --- Host header guard (DNS-rebinding defense) -------------------------------

def test_bad_host_header_rejected():
    client = _flask_client(_fake_app_ref())
    r = client.get("/", headers={"Host": "evil.example.com"})
    assert r.status_code == 400


def test_localhost_alias_accepted():
    client = _flask_client(_fake_app_ref())
    r = client.get("/", headers={"Host": "localhost:8766"})
    assert r.status_code == 200


def test_port_fallback_in_allowlist():
    """If pick_port lands on 8768, the Host check must still accept it."""
    client = _flask_client(_fake_app_ref(port=8766))
    r = client.get("/", headers={"Host": "127.0.0.1:8768"})
    assert r.status_code == 200  # 8766..8770 are all pre-approved
    r = client.get("/", headers={"Host": "127.0.0.1:8800"})
    assert r.status_code == 400  # outside the fallback window


# --- Port picker -------------------------------------------------------------

def test_pick_port_returns_preferred_when_free():
    from src.dashboard.server import pick_port
    # Pick something high and obscure to avoid collisions in CI.
    chosen = pick_port("127.0.0.1", 49321)
    assert chosen == 49321


def test_pick_port_falls_back_when_busy():
    from src.dashboard.server import pick_port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 49333))
    try:
        chosen = pick_port("127.0.0.1", 49333)
        assert chosen != 49333  # picked one of the next 4
        assert 49333 < chosen <= 49337
    finally:
        s.close()


# --- Port file round-trip ----------------------------------------------------

def test_port_file_round_trip(tmp_path):
    from src.dashboard.server import write_port_file, read_port_file
    p = tmp_path / "dashboard.port"
    write_port_file(8769, path=p)
    assert read_port_file(path=p) == 8769


def test_read_port_file_missing_returns_none(tmp_path):
    from src.dashboard.server import read_port_file
    assert read_port_file(path=tmp_path / "nope.port") is None


# --- WAL mode on History -----------------------------------------------------

def test_history_uses_wal_mode(tmp_path):
    from src.history import History
    h = History(str(tmp_path / "h.db"))
    mode = h.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
