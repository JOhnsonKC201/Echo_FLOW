"""Regression: POST handlers that parse an int `id` must not 500 on a
non-numeric/forged value. _form_int returns 0 so the handler degrades to a
graceful flash redirect."""
from __future__ import annotations

import types

import pytest

from src.history import History


def _app_ref_with_history(tmp_path):
    h = History(str(tmp_path / "h.db"))
    return types.SimpleNamespace(cfg={
        "dashboard": {"enabled": True, "host": "127.0.0.1", "port": 8766},
    }, history=h)


def _client(app_ref):
    from src.dashboard.app import make_app
    app = make_app(app_ref)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.mark.parametrize("path", [
    "/dictionary/delete",
    "/scratchpad/delete",
    "/scratchpad/save",
    "/transforms/delete",
])
def test_non_numeric_id_does_not_500(tmp_path, path):
    client = _client(_app_ref_with_history(tmp_path))
    r = client.post(path, data={"id": "abc"},
                    headers={"Host": "127.0.0.1:8766"})
    # Graceful redirect, never an unhandled 500.
    assert r.status_code in (302, 303), f"{path} returned {r.status_code}"


def test_form_int_helper_handles_garbage():
    from src.dashboard.app import _form_int
    assert _form_int({"id": "abc"}) == 0
    assert _form_int({"id": ""}) == 0
    assert _form_int({}) == 0
    assert _form_int({"id": "42"}) == 42
