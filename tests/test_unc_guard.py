"""Network-path guard: every spelling Windows treats as UNC must be refused
before anything touches it, because touching one sends the user's NetNTLM
hash to the remote host."""
from __future__ import annotations

import os

import pytest

from src import voice_actions as va
from tests.test_dashboard_actions_view import HOST, _client

pytestmark = pytest.mark.unit

UNC_SPELLINGS = [
    r"\\host\share\evil.exe",
    "//host/share/evil.exe",
    # Windows accepts either separator in either position.
    r"\/host/share/evil.exe",
    r"/\host\share\evil.exe",
    # Win32 file-namespace and device prefixes.
    r"\\?\UNC\host\share\evil.exe",
    r"\\.\pipe\evil",
    "//?/UNC/host/share",
    # Leading whitespace is not trimmed by every caller.
    r"  \\host\share\evil.exe",
    "\t//host/share",
]

LOCAL_PATHS = [
    r"C:\Windows\notepad.exe",
    "C:/Windows/notepad.exe",
    r"\Windows\notepad.exe",
    "/usr/bin/open",
    "notepad",
    "",
]


@pytest.fixture
def no_filesystem_touch(monkeypatch):
    """Fail the test if anything stats or launches a path."""
    def boom(*a, **k):
        raise AssertionError(f"touched the filesystem with {a!r}")
    for name in ("isfile", "isdir", "exists"):
        monkeypatch.setattr(os.path, name, boom)
    monkeypatch.setattr(va.hostos, "open_path", boom)
    monkeypatch.setattr(va.hostos, "open_app", boom, raising=False)


@pytest.mark.parametrize("path", UNC_SPELLINGS)
def test_every_unc_spelling_is_detected(path):
    assert va._is_unc(path)


@pytest.mark.parametrize("path", LOCAL_PATHS)
def test_local_paths_are_not_unc(path):
    assert not va._is_unc(path)


@pytest.mark.parametrize("path", UNC_SPELLINGS)
def test_dashboard_refuses_unc_app_targets(path, tmp_path, monkeypatch):
    client, app_ref = _client(tmp_path)
    touched = []
    real_isfile = os.path.isfile
    monkeypatch.setattr(os.path, "isfile",
                        lambda p: touched.append(p) or real_isfile(p) if p == path
                        else real_isfile(p))
    client.post("/actions/save", headers=HOST,
                data={"kind": "app", "name": "evil", "target": path})
    assert app_ref.history.list_action_targets("app") == []
    assert path not in touched


def _ctx(cfg):
    return va.ActionContext(focused_title=None, focused_path=None, cfg=cfg,
                            notify=lambda *a, **k: None)


@pytest.mark.parametrize("path", UNC_SPELLINGS)
def test_open_app_refuses_a_unc_target_from_config(path, no_filesystem_touch):
    cfg = {"experimental": {"action_apps": {"evil": path}}}
    ok, msg = va._h_open_app({"app": "evil"}, _ctx(cfg))
    assert not ok
    assert "network path" in msg


@pytest.mark.parametrize("path", UNC_SPELLINGS)
def test_open_folder_refuses_a_unc_target_from_config(path, no_filesystem_touch):
    cfg = {"experimental": {"action_folders": {"share": path}}}
    ok, msg = va._h_open_folder({"folder": "share"}, _ctx(cfg))
    assert not ok
    assert "isn't allowed" in msg
