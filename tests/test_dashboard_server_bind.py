"""The dashboard has no login, so it must only ever listen on loopback. A
dashboard.host edited to a LAN address would hand every device on the network
the action-target editor, which is a way to run programs."""
from __future__ import annotations

import pytest

from src.dashboard import server

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "localhost", "::1"])
def test_loopback_hosts_are_kept(host):
    assert server.loopback_only(host) == host


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "10.0.0.5",
                                  "my-laptop.local", "", None, "8.8.8.8"])
def test_anything_else_falls_back_to_127_0_0_1(host):
    assert server.loopback_only(host) == "127.0.0.1"


def test_serve_binds_loopback_even_when_config_says_all_interfaces(monkeypatch, tmp_path):
    bound = {}

    class _FakeServer:
        def serve_forever(self):
            pass

    def fake_make_server(host, port, app, threaded):
        bound["host"] = host
        return _FakeServer()

    import werkzeug.serving
    monkeypatch.setattr(werkzeug.serving, "make_server", fake_make_server)
    monkeypatch.setattr(server, "pick_port", lambda host, port: port)
    monkeypatch.setattr(server, "write_port_file", lambda port: None)
    monkeypatch.setattr("src.dashboard.app.make_app", lambda app_ref, bound_port: object())

    server.serve(app_ref=object(), host="0.0.0.0", port=8766, log_fn=lambda m: None)
    assert bound["host"] == "127.0.0.1"
