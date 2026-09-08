"""src/hostos.py: the one place Windows and macOS differ.

Every helper takes an explicit platform so both branches run on either OS.
"""
from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

from src import hostos


# --- paste chord -------------------------------------------------------------

def test_paste_is_ctrl_v_on_windows():
    assert hostos.paste_keys("win32") == ("ctrl", "v")


def test_paste_is_command_v_on_mac():
    assert hostos.paste_keys("darwin") == ("command", "v")


def test_paste_defaults_to_the_running_platform():
    assert hostos.paste_keys() == hostos.paste_keys(sys.platform)


# --- shortcuts: the command table is written in Windows chords -------------------

@pytest.mark.parametrize("combo", ["ctrl+z", "ctrl+shift+t", "alt+left", "ctrl+home"])
def test_shortcut_is_unchanged_on_windows(combo):
    assert hostos.shortcut(combo, "win32") == combo


@pytest.mark.parametrize("combo", ["ctrl+z", "alt+left"])
def test_shortcut_is_unchanged_on_linux(combo):
    assert hostos.shortcut(combo, "linux") == combo


@pytest.mark.parametrize("combo,expected", [
    ("ctrl+a", "command+a"),
    ("ctrl+shift+t", "command+shift+t"),
    ("ctrl+y", "command+shift+z"),      # redo is a different key on a Mac
    ("ctrl+home", "command+up"),
    ("ctrl+end", "command+down"),
    ("alt+left", "command+left"),
    ("alt+right", "command+right"),
    ("Ctrl + S", "command+s"),          # spacing and case are tolerated
])
def test_shortcut_translates_to_mac_chords(combo, expected):
    assert hostos.shortcut(combo, "darwin") == expected


def test_shortcut_passes_empty_through():
    assert hostos.shortcut("", "darwin") == ""


def test_shortcut_defaults_to_the_running_platform():
    assert hostos.shortcut("ctrl+z") == hostos.shortcut("ctrl+z", sys.platform)


@pytest.mark.parametrize("name,win,mac", [
    ("cmd", "Win", "Command"),
    ("win", "Win", "Command"),
    ("command", "Win", "Command"),
    ("alt", "Alt", "Option"),
    ("option", "Alt", "Option"),
    ("ctrl", "Ctrl", "Control"),
    ("shift", "Shift", "Shift"),
    ("p", "P", "P"),
    ("space", "Space", "Space"),
])
def test_modifier_label_follows_the_os(name, win, mac):
    assert hostos.modifier_label(name, "win32") == win
    assert hostos.modifier_label(name, "darwin") == mac


# --- open_path ---------------------------------------------------------------

def test_open_path_uses_startfile_on_windows(monkeypatch):
    opened = []
    monkeypatch.setattr(hostos.os, "startfile", lambda p: opened.append(p), raising=False)
    hostos.open_path(r"C:\some\folder", "win32")
    assert opened == [r"C:\some\folder"]


def test_open_path_uses_open_on_mac(monkeypatch):
    calls = []
    monkeypatch.setattr(hostos.subprocess, "Popen", lambda argv, **kw: calls.append(argv))
    hostos.open_path("/Users/me/Documents", "darwin")
    assert calls == [["open", "/Users/me/Documents"]]


def test_open_path_uses_xdg_open_elsewhere(monkeypatch):
    calls = []
    monkeypatch.setattr(hostos.subprocess, "Popen", lambda argv, **kw: calls.append(argv))
    hostos.open_path("/home/me", "linux")
    assert calls == [["xdg-open", "/home/me"]]


def test_open_path_never_uses_a_shell(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        hostos.subprocess, "Popen", lambda argv, **kw: seen.update(argv=argv, kw=kw)
    )
    hostos.open_path("/tmp/a b; rm -rf ~", "darwin")
    assert seen["argv"] == ["open", "/tmp/a b; rm -rf ~"]
    assert not seen["kw"].get("shell")


def test_open_path_propagates_failure(monkeypatch):
    def boom(argv, **kw):
        raise FileNotFoundError("no opener")
    monkeypatch.setattr(hostos.subprocess, "Popen", boom)
    with pytest.raises(FileNotFoundError):
        hostos.open_path("/x", "darwin")


# --- open_app ----------------------------------------------------------------

def test_open_app_is_mac_only():
    assert hostos.open_app("Spotify", "win32") is False


def test_open_app_reports_open_exit_status(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return types.SimpleNamespace(returncode=0 if argv[-1] == "Spotify" else 1)

    monkeypatch.setattr(hostos.subprocess, "run", fake_run)
    assert hostos.open_app("Spotify", "darwin") is True
    assert hostos.open_app("NoSuchApp", "darwin") is False
    assert calls[0] == ["open", "-a", "Spotify"]


def test_open_app_swallows_process_errors(monkeypatch):
    def boom(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 10)
    monkeypatch.setattr(hostos.subprocess, "run", boom)
    assert hostos.open_app("Spotify", "darwin") is False


# --- frontmost_title ---------------------------------------------------------

def test_frontmost_title_is_empty_on_unknown_platforms():
    assert hostos.frontmost_title("linux") == ""


def test_mac_title_is_empty_without_appkit(monkeypatch):
    monkeypatch.setitem(sys.modules, "AppKit", None)  # import raises ImportError
    assert hostos.frontmost_title("darwin") == ""


def _fake_appkit(monkeypatch, name="Slack", pid=4242):
    app = types.SimpleNamespace(
        localizedName=lambda: name, processIdentifier=lambda: pid,
    )
    ws = types.SimpleNamespace(frontmostApplication=lambda: app)
    appkit = types.ModuleType("AppKit")
    appkit.NSWorkspace = types.SimpleNamespace(sharedWorkspace=lambda: ws)
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    return app


def _fake_quartz(monkeypatch, windows):
    q = types.ModuleType("Quartz")
    q.kCGWindowListOptionOnScreenOnly = 1
    q.kCGWindowListExcludeDesktopElements = 16
    q.kCGNullWindowID = 0
    q.CGWindowListCopyWindowInfo = lambda opts, wid: windows
    monkeypatch.setitem(sys.modules, "Quartz", q)


def test_mac_title_falls_back_to_the_app_name_without_window_names(monkeypatch):
    _fake_appkit(monkeypatch, name="Slack", pid=7)
    # No Screen Recording permission: the window dict carries no kCGWindowName.
    _fake_quartz(monkeypatch, [{"kCGWindowOwnerPID": 7, "kCGWindowLayer": 0}])
    assert hostos.frontmost_title("darwin") == "Slack"


def test_mac_title_prefers_the_front_window_of_the_front_app(monkeypatch):
    _fake_appkit(monkeypatch, name="Code", pid=7)
    _fake_quartz(monkeypatch, [
        {"kCGWindowOwnerPID": 9, "kCGWindowLayer": 0, "kCGWindowName": "Other app"},
        {"kCGWindowOwnerPID": 7, "kCGWindowLayer": 25, "kCGWindowName": "Menu bar extra"},
        {"kCGWindowOwnerPID": 7, "kCGWindowLayer": 0, "kCGWindowName": "README.md"},
        {"kCGWindowOwnerPID": 7, "kCGWindowLayer": 0, "kCGWindowName": "behind"},
    ])
    assert hostos.frontmost_title("darwin") == "README.md - Code"


def test_mac_title_does_not_repeat_an_app_name_already_in_the_window(monkeypatch):
    _fake_appkit(monkeypatch, name="Slack", pid=7)
    _fake_quartz(monkeypatch, [
        {"kCGWindowOwnerPID": 7, "kCGWindowLayer": 0, "kCGWindowName": "general - Slack"},
    ])
    assert hostos.frontmost_title("darwin") == "general - Slack"


def test_mac_title_survives_a_quartz_failure(monkeypatch):
    _fake_appkit(monkeypatch, name="Mail", pid=7)
    q = types.ModuleType("Quartz")
    monkeypatch.setitem(sys.modules, "Quartz", q)  # missing constants -> AttributeError
    assert hostos.frontmost_title("darwin") == "Mail"


def test_mac_title_is_empty_when_nothing_is_frontmost(monkeypatch):
    appkit = types.ModuleType("AppKit")
    ws = types.SimpleNamespace(frontmostApplication=lambda: None)
    appkit.NSWorkspace = types.SimpleNamespace(sharedWorkspace=lambda: ws)
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    assert hostos.frontmost_title("darwin") == ""


# --- screen_size -------------------------------------------------------------

def test_screen_size_reads_the_main_screen_on_mac(monkeypatch):
    appkit = types.ModuleType("AppKit")
    frame = types.SimpleNamespace(size=types.SimpleNamespace(width=1728.0, height=1117.0))
    appkit.NSScreen = types.SimpleNamespace(
        mainScreen=lambda: types.SimpleNamespace(frame=lambda: frame)
    )
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    assert hostos.screen_size("darwin") == (1728, 1117)


def test_screen_size_is_none_when_the_toolkit_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "AppKit", None)
    assert hostos.screen_size("darwin") is None
    assert hostos.screen_size("linux") is None


# --- user_data_root / venv_python -------------------------------------------

def test_user_data_root_per_platform(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert hostos.user_data_root(platform="win32") == tmp_path / "Local" / "EchoFlow"
    assert hostos.user_data_root(platform="darwin") == (
        Path.home() / "Library" / "Application Support" / "EchoFlow"
    )
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert hostos.user_data_root(platform="linux") == tmp_path / "xdg" / "EchoFlow"


def test_venv_python_knows_both_layouts(tmp_path):
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / ".venv" / "Scripts" / "python.exe").write_text("")
    (tmp_path / ".venv" / "bin").mkdir()
    (tmp_path / ".venv" / "bin" / "python").write_text("")
    assert hostos.venv_python(tmp_path, "win32") == tmp_path / ".venv" / "Scripts" / "python.exe"
    assert hostos.venv_python(tmp_path, "darwin") == tmp_path / ".venv" / "bin" / "python"


def test_venv_python_is_none_when_absent(tmp_path):
    assert hostos.venv_python(tmp_path, "win32") is None
    assert hostos.venv_python(tmp_path, "darwin") is None


def test_detached_kwargs_never_mix_platform_flags():
    win = hostos.detached_popen_kwargs("win32")
    mac = hostos.detached_popen_kwargs("darwin")
    assert "creationflags" in win and "start_new_session" not in win
    assert mac == {"start_new_session": True}


# --- notify_native / play_sound_file ----------------------------------------

def test_notify_native_passes_text_as_argv_not_script(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(hostos.subprocess, "run", fake_run)
    nasty = 'he said "quit" \\ and \'left\''
    assert hostos.notify_native("Echo Flow", nasty, "darwin") is True
    argv = seen["argv"]
    assert argv[:2] == ["osascript", "-e"]
    assert nasty not in argv[2]           # never interpolated into the script
    assert argv[-2:] == ["Echo Flow", nasty]


def test_notify_native_is_false_off_mac_and_on_error(monkeypatch):
    assert hostos.notify_native("t", "m", "win32") is False

    def boom(argv, **kw):
        raise OSError("no osascript")
    monkeypatch.setattr(hostos.subprocess, "run", boom)
    assert hostos.notify_native("t", "m", "darwin") is False


def test_play_sound_file_uses_afplay_on_mac(monkeypatch):
    calls = []
    monkeypatch.setattr(hostos.subprocess, "Popen", lambda argv, **kw: calls.append(argv))
    assert hostos.play_sound_file("/System/Library/Sounds/Glass.aiff", "darwin") is True
    assert calls == [["afplay", "/System/Library/Sounds/Glass.aiff"]]
    assert hostos.play_sound_file("/x.wav", "win32") is False
