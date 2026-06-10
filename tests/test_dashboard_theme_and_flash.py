"""Render-time accent-color validation + flash/error output hygiene.

- accent_color is injected into a <style> block in base.html, where Jinja2's
  HTML auto-escaping is no defense (CSS injection needs no angle brackets).
  The settings save path validates the value, but config.yaml can be edited
  by hand — so the context processor must re-validate at render time.
- The graph error page interpolates an exception message into HTML; it must
  be escaped.
"""
from __future__ import annotations

import types

HOST = {"Host": "127.0.0.1:8766"}


def _fake_app_ref(accent=None):
    dash = {"enabled": True, "host": "127.0.0.1", "port": 8766, "theme": "dark"}
    if accent is not None:
        dash["accent_color"] = accent
    return types.SimpleNamespace(cfg={"dashboard": dash})


def _client(app_ref):
    from src.dashboard.app import make_app
    return make_app(app_ref).test_client()


# --- accent_color render-time validation --------------------------------------

def test_valid_accent_is_injected():
    body = _client(_fake_app_ref("#aa00ff")).get("/", headers=HOST).get_data(as_text=True)
    assert "--accent: #aa00ff" in body


def test_css_injection_accent_is_dropped():
    """A hand-edited config.yaml must not be able to smuggle CSS into every
    page. Invalid values are dropped, falling back to the default accent."""
    evil = "red; } body { visibility:hidden } x {"
    body = _client(_fake_app_ref(evil)).get("/", headers=HOST).get_data(as_text=True)
    assert "visibility:hidden" not in body
    assert "--accent:" not in body  # the override block is omitted entirely


def test_missing_accent_renders_without_override():
    body = _client(_fake_app_ref()).get("/", headers=HOST).get_data(as_text=True)
    assert "--accent:" not in body


# --- graph error page escapes the exception -----------------------------------

def test_graph_error_page_escapes_exception(monkeypatch, tmp_path):
    """graph_obsidian.render() failures surface the exception message in an
    HTML <pre>; markup inside the message must not execute."""
    from src.dashboard import graph_obsidian
    from src.history import History

    def boom(_db):
        raise RuntimeError("</pre><script>alert(1)</script>")

    # app.py imports graph_obsidian inside the route, so patch the module attr.
    monkeypatch.setattr(graph_obsidian, "render", boom)
    app_ref = _fake_app_ref()
    app_ref.history = History(str(tmp_path / "h.db"))  # route 404s without it
    r = _client(app_ref).get("/graph/raw?refresh=1", headers=HOST)
    body = r.get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


# --- flash messages survive URL-special characters ------------------------------

def test_flash_error_with_ampersand_is_not_truncated(tmp_path):
    """Older routes built ?flash={e} without URL-encoding, so a message
    containing '&' was silently truncated at the first ampersand. End-to-end:
    a snippet code-clash error whose message embeds '&' must survive the
    redirect round-trip intact."""
    import urllib.parse

    from src.history import History
    from src.dashboard import snippets as sn
    from src.dashboard.app import make_app

    h = History(str(tmp_path / "h.db"))
    app_ref = _fake_app_ref()
    app_ref.history = h
    sn.add_snippet(h.conn, "a&b", "first")
    other = sn.add_snippet(h.conn, "tmp", "second")

    client = make_app(app_ref).test_client()
    r = client.post("/snippets/update", headers=HOST,
                    data={"id": str(other), "code": "a&b", "expansion": "x"})
    assert r.status_code == 302
    q = urllib.parse.urlparse(r.headers["Location"]).query
    flash = urllib.parse.parse_qs(q).get("flash", [""])[0]
    # The full message — including everything after the '&' — must survive.
    assert flash == "another snippet already uses code 'a&b'"
