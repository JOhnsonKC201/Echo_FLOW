"""Non-blocking user notifications.

Order of preference:
1. The active pystray icon (best — appears anchored to our tray icon)
2. plyer (cross-platform fallback if available)
3. winsdk Windows.UI.Notifications (Windows 10/11)
4. Silent no-op (so we never crash on a notification)

Rate-limited: same message within 5s is suppressed to avoid spam.
"""
from __future__ import annotations

import time
import threading

from . import log as wlog
_log = wlog.get("notify")

_tray_icon = None
_last_msg: tuple[str, float] = ("", 0.0)
_lock = threading.Lock()


def set_tray(icon) -> None:
    """Wire in the pystray.Icon from TrayApp so notifications anchor to it."""
    global _tray_icon
    _tray_icon = icon


def _winsdk_toast(title: str, message: str) -> bool:
    try:
        from winsdk.windows.ui.notifications import (
            ToastNotificationManager, ToastNotification,
        )
        from winsdk.windows.data.xml.dom import XmlDocument
        xml = f"""
        <toast>
          <visual>
            <binding template="ToastGeneric">
              <text>{title}</text>
              <text>{message}</text>
            </binding>
          </visual>
        </toast>
        """.strip()
        doc = XmlDocument()
        doc.load_xml(xml)
        notifier = ToastNotificationManager.create_toast_notifier(
            "Echo Flow"
        )
        notifier.show(ToastNotification(doc))
        return True
    except Exception as e:
        _log.debug("winsdk toast failed: %s", e)
        return False


def notify(title: str, message: str, level: str = "info") -> None:
    """Show a notification. Non-blocking. Idempotent within 5s."""
    global _last_msg
    key = f"{title}|{message}"
    with _lock:
        last_key, last_t = _last_msg
        if key == last_key and (time.time() - last_t) < 5.0:
            return   # rate-limited duplicate
        _last_msg = (key, time.time())

    def _do():
        # Try pystray first
        if _tray_icon is not None:
            try:
                _tray_icon.notify(message, title)
                return
            except Exception as e:
                _log.debug("pystray notify failed: %s", e)
        # Then winsdk
        if _winsdk_toast(title, message):
            return
        # Last resort: log only
        log_fn = getattr(_log, level if level in ("info", "warning", "error") else "info")
        log_fn("TOAST [%s]: %s", title, message)

    threading.Thread(target=_do, daemon=True).start()
