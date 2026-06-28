"""Tests for the opt-in self-update check (``src/update_check.py``).

The cardinal invariant: with the feature OFF (the default), nothing touches the
network. Everything else is best-effort and must never raise.
"""
from __future__ import annotations

import pytest

from src import update_check as uc


class _Resp:
    def __init__(self, status=200, payload=None, raise_json=False):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("bad json")
        return self._payload


class _Session:
    """Minimal stand-in for a requests session — never hits the network."""

    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc
        self.calls = 0
        self.last_url = None
        self.last_kwargs = None

    def get(self, url, **kwargs):
        self.calls += 1
        self.last_url = url
        self.last_kwargs = kwargs
        if self._exc:
            raise self._exc
        return self._resp


# --------------------------------------------------------------------------- #
# version parsing / comparison
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,expected",
    [
        ("0.2.0", (0, 2, 0)),
        ("v0.2.0", (0, 2, 0)),
        ("1", (1, 0, 0)),
        ("2.5", (2, 5, 0)),
        ("v1.2.3-rc1", (1, 2, 3)),
        ("release-3.4.5", (3, 4, 5)),
        ("", (0, 0, 0)),
        ("garbage", (0, 0, 0)),
        (None, (0, 0, 0)),
    ],
)
def test_parse_version(text, expected):
    assert uc.parse_version(text) == expected


@pytest.mark.parametrize(
    "latest,current,newer",
    [
        ("0.3.0", "0.2.0", True),
        ("v0.2.1", "0.2.0", True),
        ("0.2.0", "0.2.0", False),
        ("0.1.9", "0.2.0", False),
        ("1.0.0", "0.9.9", True),
        ("garbage", "0.2.0", False),
        ("0.2.0", "garbage", True),  # any valid > unparseable
    ],
)
def test_is_newer(latest, current, newer):
    assert uc.is_newer(latest, current) is newer


# --------------------------------------------------------------------------- #
# enabled()
# --------------------------------------------------------------------------- #
def test_enabled_default_off():
    assert uc.enabled(None) is False
    assert uc.enabled({}) is False
    assert uc.enabled({"update": {}}) is False
    assert uc.enabled({"update": {"check_on_startup": False}}) is False


def test_enabled_on():
    assert uc.enabled({"update": {"check_on_startup": True}}) is True


# --------------------------------------------------------------------------- #
# check_for_update()
# --------------------------------------------------------------------------- #
def test_check_returns_newer():
    s = _Session(_Resp(200, {"tag_name": "v0.3.0", "html_url": "https://x/rel"}))
    out = uc.check_for_update("0.2.0", session=s)
    assert out == {
        "current": "0.2.0",
        "latest": "v0.3.0",
        "url": "https://x/rel",
        "is_newer": True,
    }
    assert s.last_url == uc.LATEST_API_URL


def test_check_same_version_not_newer_and_falls_back_url():
    s = _Session(_Resp(200, {"tag_name": "0.2.0"}))  # no html_url
    out = uc.check_for_update("0.2.0", session=s)
    assert out["is_newer"] is False
    assert out["url"] == uc.RELEASES_PAGE_URL


def test_check_non_200_returns_none():
    assert uc.check_for_update("0.2.0", session=_Session(_Resp(404, {}))) is None


def test_check_network_error_returns_none():
    assert uc.check_for_update("0.2.0", session=_Session(exc=RuntimeError("boom"))) is None


def test_check_bad_json_returns_none():
    assert uc.check_for_update("0.2.0", session=_Session(_Resp(200, raise_json=True))) is None


def test_check_missing_tag_returns_none():
    assert uc.check_for_update("0.2.0", session=_Session(_Resp(200, {"foo": "bar"}))) is None


# --------------------------------------------------------------------------- #
# maybe_check_async() — the off-by-default invariant
# --------------------------------------------------------------------------- #
def test_disabled_makes_no_thread_and_no_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("network must not be touched when the check is off")

    monkeypatch.setattr(uc, "check_for_update", _boom)
    t = uc.maybe_check_async(
        {"update": {"check_on_startup": False}}, "0.2.0", notify=lambda *a: None
    )
    assert t is None


def test_enabled_notifies_on_newer(monkeypatch):
    monkeypatch.setattr(
        uc,
        "check_for_update",
        lambda v, **k: {"current": v, "latest": "0.9.0", "url": "u", "is_newer": True},
    )
    seen = []
    t = uc.maybe_check_async(
        {"update": {"check_on_startup": True}},
        "0.2.0",
        notify=lambda title, msg, level: seen.append((title, msg, level)),
    )
    assert t is not None
    t.join(timeout=5)
    assert len(seen) == 1
    assert "0.9.0" in seen[0][1]
    assert seen[0][2] == "info"


def test_enabled_no_notify_when_not_newer(monkeypatch):
    monkeypatch.setattr(
        uc,
        "check_for_update",
        lambda v, **k: {"current": v, "latest": "0.1.0", "url": "u", "is_newer": False},
    )
    seen = []
    t = uc.maybe_check_async(
        {"update": {"check_on_startup": True}}, "0.2.0", notify=lambda *a: seen.append(a)
    )
    t.join(timeout=5)
    assert seen == []


def test_notify_suppressed_when_notify_false(monkeypatch):
    monkeypatch.setattr(
        uc,
        "check_for_update",
        lambda v, **k: {"current": v, "latest": "9.9.9", "url": "u", "is_newer": True},
    )
    seen = []
    t = uc.maybe_check_async(
        {"update": {"check_on_startup": True, "notify": False}},
        "0.2.0",
        notify=lambda *a: seen.append(a),
    )
    t.join(timeout=5)
    assert seen == []


# --------------------------------------------------------------------------- #
# the privacy ledger must reflect the check honestly
# --------------------------------------------------------------------------- #
def test_privacy_ledger_reflects_update_check():
    from src.dashboard import privacy

    off = privacy.update_check_state({"update": {"check_on_startup": False}})
    assert off["enabled"] is False
    assert off["endpoint"] is None

    on = privacy.update_check_state({"update": {"check_on_startup": True}})
    assert on["enabled"] is True
    assert "github" in on["endpoint"].lower()
