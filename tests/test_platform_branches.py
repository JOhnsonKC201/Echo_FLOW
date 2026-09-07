"""The macOS branches in the modules that used to be Windows-only.

Each test drives the other platform's path explicitly (a `platform=` argument
or a patched `hostos` flag), so the whole file runs on Windows and on the
macOS CI job alike. Nothing here touches the real OS.
"""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from src import hostos


# --- inject: the paste chord -------------------------------------------------

def _paste_with(monkeypatch, platform: str) -> list[tuple]:
    from src import inject
    import pyautogui
    calls: list[tuple] = []
    monkeypatch.setattr(pyautogui, "hotkey", lambda *keys: calls.append(keys))
    monkeypatch.setattr(inject.pyperclip, "copy", lambda t: None)
    monkeypatch.setattr(inject.time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "platform", platform)
    inject.Injector(restore_clipboard=False)._paste("hello")
    return calls


def test_paste_sends_ctrl_v_on_windows(monkeypatch):
    assert _paste_with(monkeypatch, "win32") == [("ctrl", "v")]


def test_paste_sends_command_v_on_mac(monkeypatch):
    assert _paste_with(monkeypatch, "darwin") == [("command", "v")]


def test_focused_title_goes_through_hostos(monkeypatch):
    from src import inject
    monkeypatch.setattr(hostos, "frontmost_title", lambda platform=None: "README.md - Code")
    assert inject.Injector().focused_title() == "README.md - Code"


# --- hotkey: Command and Option ---------------------------------------------

def test_combo_accepts_mac_spellings():
    from pynput import keyboard
    from src.hotkey import _parse_combo
    assert _parse_combo("command+shift") == {keyboard.Key.cmd, keyboard.Key.shift}
    assert _parse_combo("option+space") == {keyboard.Key.alt, keyboard.Key.space}
    assert _parse_combo("cmd+shift") == _parse_combo("win+shift")


def test_left_and_right_command_normalize_to_cmd():
    from pynput import keyboard
    from src.hotkey import HotkeyListener
    hl = HotkeyListener("cmd+shift", "hold", lambda: None)
    assert hl._norm(keyboard.Key.cmd_l) is keyboard.Key.cmd
    assert hl._norm(keyboard.Key.cmd_r) is keyboard.Key.cmd
    assert hl._norm(keyboard.Key.space) is keyboard.Key.space


# --- main: which loop owns the main thread -------------------------------------

class _FakeThread:
    started: list = []

    def __init__(self, target=None, daemon=None):
        self.target = target

    def start(self):
        _FakeThread.started.append(self.target)


def _serve(platform: str) -> tuple[list, list]:
    from src.main import _serve_ui
    _FakeThread.started = []
    inline: list = []
    _serve_ui(lambda: inline.append("listener"), lambda: inline.append("tray"),
              platform=platform, thread_factory=_FakeThread)
    return inline, _FakeThread.started


def test_windows_blocks_on_the_listener_and_starts_no_thread():
    inline, threads = _serve("win32")
    assert inline == ["listener"]
    assert threads == []


def test_mac_moves_the_listener_to_a_thread_and_blocks_on_the_tray():
    inline, threads = _serve("darwin")
    assert inline == ["tray"]
    assert len(threads) == 1
    threads[0]()
    assert inline == ["tray", "listener"]


def test_serve_ui_defaults_to_a_real_thread_type():
    import inspect
    from src.main import _serve_ui
    assert inspect.signature(_serve_ui).parameters["thread_factory"].default is threading.Thread


# --- watchdog: relaunch command ----------------------------------------------------

def test_relaunch_uses_the_vbs_on_windows(tmp_path):
    from src import watchdog
    (tmp_path / "run_silent.vbs").write_text("")
    assert watchdog._relaunch_command(tmp_path, "win32") == [
        "wscript.exe", str(tmp_path / "run_silent.vbs")
    ]


def test_relaunch_is_a_noop_on_windows_without_the_vbs(tmp_path):
    from src import watchdog
    assert watchdog._relaunch_command(tmp_path, "win32") is None


def test_relaunch_runs_the_venv_interpreter_on_mac(tmp_path):
    from src import watchdog
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    py = tmp_path / ".venv" / "bin" / "python"
    py.write_text("")
    assert watchdog._relaunch_command(tmp_path, "darwin") == [str(py), "-m", "src.main"]


def test_relaunch_falls_back_to_this_interpreter_without_a_venv(tmp_path):
    from src import watchdog
    assert watchdog._relaunch_command(tmp_path, "darwin") == [sys.executable, "-m", "src.main"]


def test_relaunch_detaches_off_windows(monkeypatch, tmp_path):
    from src import watchdog
    seen = {}
    monkeypatch.setattr(watchdog.hostos, "IS_WINDOWS", False)
    monkeypatch.setattr(watchdog, "_relaunch_command", lambda cwd: ["py", "-m", "src.main"])
    monkeypatch.setattr(watchdog.hostos, "detached_popen_kwargs",
                        lambda platform=None: {"start_new_session": True})
    monkeypatch.setattr(watchdog.subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd, kw=kw))
    watchdog._relaunch()
    assert seen["cmd"] == ["py", "-m", "src.main"]
    assert seen["kw"]["start_new_session"] is True
    assert seen["kw"]["stdout"] is subprocess.DEVNULL


# --- ollama_supervisor: finding and starting the Mac install -----------------------

def test_finds_the_app_bundle_on_mac(monkeypatch, tmp_path):
    from src import ollama_supervisor as sup
    app = tmp_path / "Ollama.app"
    app.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nothing"))
    monkeypatch.setattr(sup, "_EXTRA_DIRS", ())
    monkeypatch.setattr(sup, "_MAC_CANDIDATES", (app, tmp_path / "bin" / "ollama"))
    monkeypatch.setattr(sup.hostos, "is_mac", lambda platform=None: True)
    monkeypatch.setattr(sup.shutil, "which", lambda n: None)
    assert sup.find_ollama() == app


def test_prefers_the_app_bundle_over_the_cli_on_mac(monkeypatch, tmp_path):
    from src import ollama_supervisor as sup
    app = tmp_path / "Ollama.app"
    app.mkdir()
    cli = tmp_path / "bin" / "ollama"
    cli.parent.mkdir()
    cli.write_text("")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nothing"))
    monkeypatch.setattr(sup, "_EXTRA_DIRS", ())
    monkeypatch.setattr(sup, "_MAC_CANDIDATES", (app, cli))
    monkeypatch.setattr(sup.hostos, "is_mac", lambda platform=None: True)
    assert sup.find_ollama() == app


def test_a_bare_app_directory_name_is_not_a_binary(tmp_path):
    from src import ollama_supervisor as sup
    assert sup._is_installed(tmp_path / "missing.app") is False
    assert sup._is_installed(tmp_path) is False       # a dir without .app
    f = tmp_path / "ollama"
    f.write_text("")
    assert sup._is_installed(f) is True


def test_mac_candidates_are_ignored_on_windows(monkeypatch, tmp_path):
    from src import ollama_supervisor as sup
    app = tmp_path / "Ollama.app"
    app.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nothing"))
    monkeypatch.setattr(sup, "_EXTRA_DIRS", ())
    monkeypatch.setattr(sup, "_MAC_CANDIDATES", (app,))
    monkeypatch.setattr(sup.hostos, "is_mac", lambda platform=None: False)
    monkeypatch.setattr(sup.shutil, "which", lambda n: None)
    assert sup.find_ollama() is None


def _spawn_args(monkeypatch, exe: Path) -> tuple[list, dict]:
    from src import ollama_supervisor as sup
    seen = {}
    monkeypatch.setattr(sup.subprocess, "Popen", lambda args, **kw: seen.update(args=args, kw=kw))
    assert sup._spawn(exe) is True
    return seen["args"], seen["kw"]


def test_spawn_opens_the_app_bundle(monkeypatch, tmp_path):
    args, _ = _spawn_args(monkeypatch, tmp_path / "Ollama.app")
    assert args == ["open", "-a", str(tmp_path / "Ollama.app")]


def test_spawn_serves_with_a_bare_binary(monkeypatch, tmp_path):
    args, _ = _spawn_args(monkeypatch, tmp_path / "ollama")
    assert args == [str(tmp_path / "ollama"), "serve"]


def test_spawn_runs_the_windows_tray_app_as_is(monkeypatch, tmp_path):
    args, _ = _spawn_args(monkeypatch, tmp_path / "ollama app.exe")
    assert args == [str(tmp_path / "ollama app.exe")]


# --- sound: macOS system sounds ----------------------------------------------------

@pytest.fixture
def mac_sounds(monkeypatch, tmp_path):
    from src import sound
    monkeypatch.setattr(sound.hostos, "IS_MAC", True)
    monkeypatch.setattr(sound.hostos, "IS_WINDOWS", False)
    for name in ("Glass", "Tink", "Pop"):
        (tmp_path / f"{name}.aiff").write_bytes(b"")
    monkeypatch.setattr(sound, "_system_sound_dirs", lambda: [str(tmp_path)])
    played: list[str] = []
    monkeypatch.setattr(sound.hostos, "play_sound_file",
                        lambda path, platform=None: played.append(path) or True)
    return sound, tmp_path, played


def test_windows_alias_maps_to_a_mac_system_sound(mac_sounds):
    sound, folder, played = mac_sounds
    assert sound._play_alias_or_file("SystemNotification") is True
    assert played == [str(folder / "Glass.aiff")]


def test_bare_mac_sound_name_resolves_with_or_without_suffix(mac_sounds):
    sound, folder, played = mac_sounds
    assert sound._play_alias_or_file("Tink") is True
    assert sound._play_alias_or_file("Pop.aiff") is True
    assert played == [str(folder / "Tink.aiff"), str(folder / "Pop.aiff")]


def test_unknown_mac_sound_is_reported_not_raised(mac_sounds):
    sound, _, played = mac_sounds
    assert sound._play_alias_or_file("NoSuchSound") is False
    assert played == []


def test_mac_paths_keep_forward_slashes(monkeypatch, tmp_path):
    from src import sound
    monkeypatch.setattr(sound.hostos, "IS_WINDOWS", False)
    monkeypatch.setattr(sound.hostos, "IS_MAC", True)
    f = tmp_path / "custom.wav"
    f.write_bytes(b"")
    assert sound._resolve_wav(str(f).replace("\\", "/")) is not None


def test_mac_catalog_is_listed_first_on_mac(monkeypatch):
    from src import sound
    monkeypatch.setattr(sound.hostos, "IS_MAC", True)
    monkeypatch.setattr(sound.hostos, "IS_WINDOWS", False)
    monkeypatch.setattr(sound, "_system_sound_dirs", lambda: [])
    values = [c["value"] for c in sound.list_choices()]
    assert values[: len(sound.MAC_SOUND_CHOICES)] == [v for v, _ in sound.MAC_SOUND_CHOICES]
    assert "SystemNotification" in values      # the Windows aliases still map


def test_windows_catalog_is_unchanged_off_mac(monkeypatch):
    from src import sound
    monkeypatch.setattr(sound.hostos, "IS_MAC", False)
    values = [c["value"] for c in sound.list_choices()]
    assert values == [v for v, _ in sound.SOUND_CHOICES]


# --- voice_actions: opening things -----------------------------------------------

def test_open_folder_reports_an_opener_failure(tmp_path, monkeypatch):
    from src import voice_actions as va

    def boom(p, platform=None):
        raise OSError("no opener")

    monkeypatch.setattr(va.hostos, "open_path", boom)
    cfg = {"experimental": {"action_folders": {"docs": str(tmp_path)}}}
    ctx = va.ActionContext(focused_title=None, focused_path=None, cfg=cfg,
                           notify=lambda *a, **k: None)
    ok, msg = va.dispatch(va.ActionMatch("open_folder", "Open docs", {"folder": "docs"}), ctx)
    assert ok is False and "no opener" in msg


def _mac_launch_env(monkeypatch):
    """No PATH hit, no process, no Windows branch: only the macOS fallback is live."""
    import shutil

    def not_found(argv, **kw):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(subprocess, "Popen", not_found)
    monkeypatch.setattr(shutil, "which", lambda n: None)
    monkeypatch.setattr(sys, "platform", "darwin")
    from src import voice_actions as va
    monkeypatch.setattr(va.hostos, "IS_MAC", True)
    return va


def test_launch_falls_back_to_open_a_on_mac(monkeypatch):
    va = _mac_launch_env(monkeypatch)
    opened = []
    monkeypatch.setattr(va.hostos, "open_app", lambda name, platform=None: opened.append(name) or True)
    ok, msg = va._launch_executable("Spotify", "spotify")
    assert ok is True and opened == ["spotify"]


def test_launch_reports_when_open_a_finds_nothing(monkeypatch):
    va = _mac_launch_env(monkeypatch)
    monkeypatch.setattr(va.hostos, "open_app", lambda name, platform=None: False)
    ok, msg = va._launch_executable("Spotify", "spotify")
    assert ok is False and "Spotify" in msg


def test_launch_never_hands_a_command_string_to_open_a(monkeypatch):
    va = _mac_launch_env(monkeypatch)
    monkeypatch.setattr(va.hostos, "open_app", lambda name, platform=None: pytest.fail("must not"))
    ok, _ = va._launch_executable("Calc", "cmd /c calc")
    assert ok is False


# --- notify: Notification Center ---------------------------------------------------

def test_notify_uses_notification_center_before_winsdk(monkeypatch):
    from src import notify as wn
    wn._last_msg = ("", 0.0)
    wn.set_tray(None)
    done = threading.Event()
    seen = []

    def native(title, message, platform=None):
        seen.append((title, message))
        done.set()
        return True

    monkeypatch.setattr(wn.hostos, "notify_native", native)
    monkeypatch.setattr(wn, "_winsdk_toast", lambda *a, **k: pytest.fail("winsdk reached"))
    wn.notify("Echo Flow", "Ready", "info")
    assert done.wait(2.0)
    assert seen == [("Echo Flow", "Ready")]
    wn._last_msg = ("", 0.0)


# --- dashboard window: screen size -------------------------------------------------

def test_window_size_falls_back_when_the_screen_is_unknown(monkeypatch):
    from src.dashboard import window as W
    monkeypatch.setattr(W.hostos, "screen_size", lambda platform=None: None)
    assert W._primary_screen_size() == (1920, 1080)
    monkeypatch.setattr(W.hostos, "screen_size", lambda platform=None: (2560, 1440))
    assert W._primary_screen_size() == (2560, 1440)
