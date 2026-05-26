"""Echo Flow desktop app — native window + system tray + global hotkey.

Behavior
--------
* Flask dashboard runs on a background thread.
* pywebview owns the main thread and presents the UI as a real OS window.
* pystray adds a tray icon (Show / Hide / Reload / Always-on-top / Quit).
* Global hotkey (default Ctrl+Alt+Space; overridable via
  ``dashboard.hotkey`` in config.yaml) toggles the window from anywhere.
* Closing the window hides it to the tray instead of quitting — "Quit" in
  the tray menu is the only way to fully exit.
* Window size is persisted to ``data/dashboard_window.json`` (size only;
  position is recomputed to center on the primary monitor each launch so
  unplugging a second monitor never strands the window off-screen).
* A lightweight splash screen renders instantly so the user never sees a
  white flash while Flask warms up.

Use:  .venv\\Scripts\\python.exe app.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

if getattr(sys, "frozen", False):
    # PyInstaller one-folder: bundled data sits next to the .exe in _MEIPASS for
    # read-only resources; user data (config.yaml, history.db) lives next to .exe.
    BUNDLE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    REPO = Path(sys.executable).parent
    # Make bundled `src/` importable.
    sys.path.insert(0, str(BUNDLE))
else:
    REPO = Path(__file__).resolve().parent
    BUNDLE = REPO
    sys.path.insert(0, str(REPO))

import webview
import yaml
from PIL import Image
from pynput import keyboard
import pystray
from werkzeug.serving import make_server

from src.dashboard.app import make_app
from src.dashboard.server import pick_port, write_port_file
from src.history import History


DEFAULT_HOTKEY = "<ctrl>+<alt>+<space>"
ICON_PATH = BUNDLE / "assets" / "icon.png"
WINDOW_STATE_PATH = REPO / "data" / "dashboard_window.json"

# Inline splash — shown instantly while Flask warms up. Stays on the same
# background color as the dashboard so there is zero white flash.
_SPLASH_HTML = """<!doctype html>
<html><head><meta charset='utf-8'><title>Echo Flow</title>
<style>
  html,body{margin:0;height:100%;background:#0d1014;color:#e7e9ee;
    font:500 13px/1.4 'Inter','Segoe UI',system-ui,sans-serif;
    -webkit-font-smoothing:antialiased;overflow:hidden;}
  .wrap{position:absolute;inset:0;display:flex;flex-direction:column;
    align-items:center;justify-content:center;gap:18px;}
  .logo{font:600 28px/1 'JetBrains Mono',ui-monospace,Consolas,monospace;
    color:#3eaf6f;letter-spacing:-0.02em;}
  .name{font-size:14px;font-weight:600;letter-spacing:0.02em;}
  .dots{display:flex;gap:6px;margin-top:4px;}
  .dots span{width:6px;height:6px;border-radius:50%;background:#3eaf6f;
    opacity:.25;animation:p 1.2s infinite ease-in-out;}
  .dots span:nth-child(2){animation-delay:.15s}
  .dots span:nth-child(3){animation-delay:.3s}
  @keyframes p{0%,80%,100%{opacity:.25;transform:scale(.85)}
                40%{opacity:1;transform:scale(1)}}
</style></head><body><div class='wrap'>
  <div class='logo'>&#9646;&#9646;&#9647;</div>
  <div class='name'>Echo Flow</div>
  <div class='dots'><span></span><span></span><span></span></div>
</div></body></html>"""


class _AppRef:
    """Minimal stand-in for src.main.App — only the attrs the dashboard reads."""

    def __init__(self, cfg, cfg_path, history):
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.history = history
        self._scratchpad_target_id = None

    def reload_config(self):
        pass

    def refresh_transform_hotkeys(self):
        pass


class DesktopApp:
    def __init__(self):
        self.window: webview.Window | None = None
        self.server = None
        self.tray: pystray.Icon | None = None
        self.hotkey_listener: keyboard.GlobalHotKeys | None = None
        self._quitting = False
        self.cfg: dict = {}
        self.hotkey: str = DEFAULT_HOTKEY
        self.url: str = ""
        self._on_top: bool = False

    # ---------- Flask server ----------
    def _start_server(self) -> str:
        cfg_path = REPO / "config.yaml"
        if not cfg_path.exists() and (BUNDLE / "config.yaml").exists():
            import shutil
            shutil.copy(BUNDLE / "config.yaml", cfg_path)
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        self.cfg = cfg
        dash_cfg = cfg.get("dashboard", {}) or {}
        # Hotkey is configurable; fall back to default on bad/empty values.
        hk = dash_cfg.get("hotkey")
        if isinstance(hk, str) and hk.strip():
            self.hotkey = hk.strip()
        db_path = REPO / cfg.get("history", {}).get("db_path", "data/history.db")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        history = History(str(db_path))
        app_ref = _AppRef(cfg, cfg_path, history)

        host = dash_cfg.get("host", "127.0.0.1")
        pref = int(dash_cfg.get("port", 8766))
        port = pick_port(host, pref)
        write_port_file(port)

        flask_app = make_app(app_ref)
        self.server = make_server(host, port, flask_app, threaded=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://{host}:{port}/"

    # ---------- Window state persistence ----------
    @staticmethod
    def _load_window_state() -> dict:
        try:
            if WINDOW_STATE_PATH.exists():
                data = json.loads(WINDOW_STATE_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _save_window_state(self) -> None:
        if not self.window:
            return
        try:
            w, h = None, None
            try:
                size = self.window.get_size()
                if size:
                    w, h = int(size[0]), int(size[1])
            except Exception:
                pass
            if not (w and h):
                return
            state = {"width": w, "height": h}
            WINDOW_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            WINDOW_STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _primary_screen_size() -> tuple[int, int]:
        """Best-effort primary monitor size (Win11 only). Falls back to 1920x1080."""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            user32.SetProcessDPIAware()
            return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
        except Exception:
            return 1920, 1080

    # ---------- Window controls ----------
    def show_window(self):
        if not self.window:
            return
        try:
            self.window.show()
            self.window.restore()
        except Exception:
            pass

    def hide_window(self):
        if self.window:
            try:
                self.window.hide()
            except Exception:
                pass

    def toggle_window(self):
        if not self.window:
            return
        # pywebview doesn't expose visibility cleanly across platforms — track ourselves.
        if getattr(self, "_visible", True):
            self.hide_window()
            self._visible = False
        else:
            self.show_window()
            self._visible = True

    def _on_closing(self) -> bool:
        """Intercept window close — hide to tray instead of quitting."""
        # Always snapshot size before hiding/closing.
        self._save_window_state()
        if self._quitting:
            return True
        self.hide_window()
        self._visible = False
        return False

    def reload_window(self):
        """Dev convenience — re-navigate to the dashboard root."""
        if self.window and self.url:
            try:
                self.window.load_url(self.url)
            except Exception:
                pass

    def toggle_on_top(self):
        if not self.window:
            return
        self._on_top = not self._on_top
        try:
            self.window.on_top = self._on_top
        except Exception:
            pass

    def _hotkey_label(self) -> str:
        """Pretty-print the pynput hotkey spec for the tray submenu."""
        return (self.hotkey
                .replace("<", "").replace(">", "")
                .replace("+", " + ")
                .title())

    # ---------- Tray ----------
    def _build_tray(self) -> pystray.Icon:
        icon_img = Image.open(ICON_PATH)
        menu = pystray.Menu(
            pystray.MenuItem("Show Echo Flow",
                             lambda i, item: self.show_window(),
                             default=True),
            pystray.MenuItem("Hide", lambda i, item: self.hide_window()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"Toggle: {self._hotkey_label()}",
                             lambda i, item: self.toggle_window()),
            pystray.MenuItem("Always on top",
                             lambda i, item: self.toggle_on_top(),
                             checked=lambda item: self._on_top),
            pystray.MenuItem("Reload window",
                             lambda i, item: self.reload_window()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Echo Flow", lambda i, item: self.quit()),
        )
        return pystray.Icon("echo-flow", icon_img, "Echo Flow", menu=menu)

    # ---------- Global hotkey ----------
    def _start_hotkey(self):
        try:
            self.hotkey_listener = keyboard.GlobalHotKeys(
                {self.hotkey: self.toggle_window})
            self.hotkey_listener.start()
        except Exception as e:
            print(f"[hotkey] failed to register {self.hotkey!r}: {e}; "
                  f"falling back to {DEFAULT_HOTKEY}")
            if self.hotkey != DEFAULT_HOTKEY:
                try:
                    self.hotkey = DEFAULT_HOTKEY
                    self.hotkey_listener = keyboard.GlobalHotKeys(
                        {self.hotkey: self.toggle_window})
                    self.hotkey_listener.start()
                except Exception as e2:
                    print(f"[hotkey] fallback also failed: {e2}")

    # ---------- Lifecycle ----------
    def quit(self):
        self._quitting = True
        try:
            if self.hotkey_listener:
                self.hotkey_listener.stop()
        except Exception:
            pass
        try:
            if self.server:
                self.server.shutdown()
        except Exception:
            pass
        try:
            if self.tray:
                self.tray.stop()
        except Exception:
            pass
        try:
            if self.window:
                self.window.destroy()
        except Exception:
            pass

    def _wait_for_server(self, url: str, timeout_s: float = 8.0) -> bool:
        """Poll the dashboard root until it answers, so we can swap from splash."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=0.4) as resp:
                    if resp.status < 500:
                        return True
            except Exception:
                time.sleep(0.1)
        return False

    def _on_started(self):
        """pywebview lifecycle hook: server is warming up, swap splash → app."""
        try:
            if self._wait_for_server(self.url, timeout_s=8.0):
                self.window.load_url(self.url)
        except Exception:
            pass

    def run(self) -> int:
        self.url = self._start_server()

        # Restore previous size; center on primary monitor every launch.
        state = self._load_window_state()
        width = int(state.get("width", 1200))
        height = int(state.get("height", 780))
        sw, sh = self._primary_screen_size()
        x = max(0, (sw - width) // 2)
        y = max(0, (sh - height) // 2)

        # Render splash *inline* so it paints on the very first frame —
        # zero white flash, even before Flask is reachable.
        self.window = webview.create_window(
            title="Echo Flow",
            html=_SPLASH_HTML,
            width=width,
            height=height,
            x=x,
            y=y,
            min_size=(960, 600),
            background_color="#0d1014",
            text_select=True,
        )
        self.window.events.closing += self._on_closing
        self._visible = True

        # Tray needs its own thread; pywebview must own the main thread on Windows.
        self.tray = self._build_tray()
        threading.Thread(target=self.tray.run, daemon=True).start()

        self._start_hotkey()

        try:
            # `func=` runs on a background thread once the GUI is up — perfect
            # spot to poll Flask and swap the splash for the real dashboard.
            webview.start(func=self._on_started)
        finally:
            self.quit()
        return 0


def main() -> int:
    return DesktopApp().run()


if __name__ == "__main__":
    sys.exit(main())
