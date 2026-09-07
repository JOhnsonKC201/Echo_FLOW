"""Where Windows and macOS differ, in one place.

Echo Flow was built on Windows. Every call that only exists there (the Win32
foreground window, ``os.startfile``, ``%LOCALAPPDATA%``, the venv's ``Scripts``
layout) goes through this module, so the rest of the code asks a question
("what pastes?", "open this folder") instead of testing ``sys.platform``.

Every helper takes an optional ``platform`` argument so the branch for the
other OS can be exercised in tests on either machine. Nothing here imports a
platform-only library at module load: they are imported inside the function
that needs them, and every failure degrades to the harmless answer (an empty
title, ``None``, ``False``) rather than an exception on the dictation path.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

WINDOWS = "win32"
MAC = "darwin"

IS_WINDOWS = sys.platform == WINDOWS
IS_MAC = sys.platform == MAC


def _plat(platform: str | None) -> str:
    return platform or sys.platform


def is_windows(platform: str | None = None) -> bool:
    return _plat(platform) == WINDOWS


def is_mac(platform: str | None = None) -> bool:
    return _plat(platform) == MAC


# --- keyboard ---------------------------------------------------------------

def paste_keys(platform: str | None = None) -> tuple[str, str]:
    """The chord that pastes the clipboard into the focused app."""
    return ("command", "v") if is_mac(platform) else ("ctrl", "v")


# --- opening things ----------------------------------------------------------

def open_path(path: str, platform: str | None = None) -> None:
    """Open a file or folder the way a double-click would.

    Raises on failure so the caller can report it. Never goes through a shell:
    the path is one argv element, whatever characters it holds.
    """
    p = _plat(platform)
    if p == WINDOWS:
        os.startfile(path)  # type: ignore[attr-defined]
        return
    opener = "open" if p == MAC else "xdg-open"
    subprocess.Popen(
        [opener, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def open_app(name: str, platform: str | None = None) -> bool:
    """macOS only: launch an application by name, as Spotlight would.

    ``open -a`` resolves "Spotify" to /Applications/Spotify.app without the
    caller knowing where it lives. Returns False on any other OS, or when
    ``open`` reports that no such application exists.
    """
    if not is_mac(platform):
        return False
    try:
        done = subprocess.run(
            ["open", "-a", name], capture_output=True, timeout=10,
        )
        return done.returncode == 0
    except Exception:
        return False


# --- what the user is looking at ------------------------------------------------

def frontmost_title(platform: str | None = None) -> str:
    """Title of the focused window, or "" when the OS will not say.

    On Windows this is the Win32 foreground window text. On macOS it is the
    front window's title when Screen Recording permission has been granted
    (that is what gates window names), otherwise the application name, which
    is enough for the app-aware cleanup profiles to tell Slack from VS Code.
    """
    p = _plat(platform)
    if p == WINDOWS:
        return _windows_title()
    if p == MAC:
        return _mac_title()
    return ""


def _windows_title() -> str:
    try:
        import win32gui
        return win32gui.GetWindowText(win32gui.GetForegroundWindow()) or ""
    except Exception:
        return ""


def _mac_title() -> str:
    try:
        from AppKit import NSWorkspace
    except Exception:
        return ""
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return ""
        app_name = str(app.localizedName() or "")
        pid = int(app.processIdentifier())
    except Exception:
        return ""
    window = _mac_window_name(pid)
    if window and app_name and app_name.lower() not in window.lower():
        return f"{window} - {app_name}"
    return window or app_name


def _mac_window_name(pid: int) -> str:
    """Title of ``pid``'s front window, or "".

    ``kCGWindowName`` is only present when the user has granted Screen
    Recording permission (macOS 10.15 and later); without it the key is simply
    absent, and the caller falls back to the application name.
    """
    try:
        import Quartz
        opts = (
            Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements
        )
        # Front to back, so the first layer-0 window owned by pid is its front one.
        for info in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or ():
            if int(info.get("kCGWindowOwnerPID", -1)) != pid:
                continue
            if int(info.get("kCGWindowLayer", 1)) != 0:
                continue
            name = info.get("kCGWindowName")
            if name:
                return str(name)
    except Exception:
        pass
    return ""


def screen_size(platform: str | None = None) -> tuple[int, int] | None:
    """Primary display size, or None when it cannot be read."""
    p = _plat(platform)
    try:
        if p == WINDOWS:
            import ctypes
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            user32.SetProcessDPIAware()
            return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
        if p == MAC:
            from AppKit import NSScreen
            frame = NSScreen.mainScreen().frame()
            return int(frame.size.width), int(frame.size.height)
    except Exception:
        return None
    return None


# --- files and processes --------------------------------------------------------

def user_data_root(app_name: str = "EchoFlow", platform: str | None = None) -> Path:
    """Per-user writable directory for a packaged install."""
    p = _plat(platform)
    if p == WINDOWS:
        base = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(base) / app_name
    if p == MAC:
        return Path.home() / "Library" / "Application Support" / app_name
    base = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(base) / app_name


def venv_python(root: Path, platform: str | None = None) -> Path | None:
    """The interpreter inside ``root/.venv`` in this OS's layout, or None."""
    rel = Path("Scripts") / "python.exe" if is_windows(platform) else Path("bin") / "python"
    candidate = Path(root) / ".venv" / rel
    return candidate if candidate.exists() else None


def detached_popen_kwargs(platform: str | None = None) -> dict:
    """Popen keyword arguments for a child that must outlive this process."""
    if is_windows(platform):
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, and no console window.
        return {"creationflags": 0x00000008 | 0x00000200}
    return {"start_new_session": True}


# --- feedback ---------------------------------------------------------------

def notify_native(title: str, message: str, platform: str | None = None) -> bool:
    """macOS Notification Center through osascript.

    The title and message travel as argv items and are read back inside the
    script, so a quote or backslash in a dictation can never break out of it.
    """
    if not is_mac(platform):
        return False
    script = (
        "on run argv\n"
        "display notification (item 2 of argv) with title (item 1 of argv)\n"
        "end run"
    )
    try:
        done = subprocess.run(
            ["osascript", "-e", script, title, message],
            capture_output=True, timeout=5,
        )
        return done.returncode == 0
    except Exception:
        return False


def play_sound_file(path: str, platform: str | None = None) -> bool:
    """Play an audio file without blocking. macOS uses the bundled afplay."""
    if not is_mac(platform):
        return False
    try:
        subprocess.Popen(
            ["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False
