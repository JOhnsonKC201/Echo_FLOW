"""What the phone bridge hands out and accepts. The bridge can be exposed on
the LAN over plain HTTP, so it shares only what the phone itself produced and
refuses oversized cleanup jobs that would tie up the model."""
from __future__ import annotations

import pytest

from src import bridge
from tests.test_bridge import client_and_app  # noqa: F401  (fixture)

pytestmark = pytest.mark.unit

KEY = {"X-Echo-Key": "test-key"}


def _log(history, text, source):
    history.log(window_title="w", style="casual", language="en",
                duration_ms=1000, raw_text=text, cleaned_text=text, source=source)


def test_history_returns_only_mobile_dictations(client_and_app):
    client, app_ref = client_and_app
    _log(app_ref.history, "typed on the phone", "mobile")
    _log(app_ref.history, "private desktop email", "desktop")
    items = client.get("/v1/history?limit=50", headers=KEY).get_json()["items"]
    assert [i["cleaned"] for i in items] == ["typed on the phone"]


def test_history_limit_counts_mobile_rows_only(client_and_app):
    client, app_ref = client_and_app
    for i in range(3):
        _log(app_ref.history, f"phone {i}", "mobile")
    for i in range(10):
        _log(app_ref.history, f"desk {i}", "desktop")
    items = client.get("/v1/history?limit=3", headers=KEY).get_json()["items"]
    assert len(items) == 3
    assert all(i["cleaned"].startswith("phone") for i in items)


def test_cleanup_rejects_text_over_the_limit(client_and_app):
    client, app_ref = client_and_app
    called = []
    app_ref.cleaner.clean = lambda *a, **k: called.append(1) or ("x", False)
    r = client.post("/v1/cleanup", headers=KEY,
                    json={"text": "a" * (bridge.MAX_CLEANUP_CHARS + 1)})
    assert r.status_code == 413
    assert called == []


def test_cleanup_accepts_text_at_the_limit(client_and_app):
    client, _ = client_and_app
    r = client.post("/v1/cleanup", headers=KEY,
                    json={"text": "a" * bridge.MAX_CLEANUP_CHARS})
    assert r.status_code == 200
