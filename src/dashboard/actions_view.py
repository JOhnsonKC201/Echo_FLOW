"""Page-data helper for the top-level /actions section (Phase 14 Action Mode).

Read-only view over voice_actions.list_supported() + the configured
action_apps map + the history.voice_actions log populated by main.py's
dispatch site. Mirrors commands_view.page_data.
"""
from __future__ import annotations

import time


def page_data(cfg: dict, history) -> dict:
    """Build the payload for templates/actions.html.

    Returns:
        {
          "enabled": bool,
          "prefix": str,
          "email_url": str,
          "apps": list[dict],     # {"name", "target"} from action_apps
          "supported": list[str],
          "recent": list[dict],   # most recent voice_actions entries
        }
    """
    from .. import voice_actions as _va
    exp = (cfg or {}).get("experimental", {}) or {}
    enabled = bool(exp.get("action_mode", False))
    prefix = exp.get("command_prefix", "computer")
    email_url = exp.get("action_email_url", "https://mail.google.com")
    apps_map = exp.get("action_apps", {}) or {}
    apps = [{"name": str(k), "target": str(v)} for k, v in apps_map.items()]

    recent: list[dict] = []
    if history is not None and getattr(history, "conn", None) is not None:
        try:
            for row in history.recent_actions(limit=30):
                ts = row.get("ts") or 0
                try:
                    row["ts_human"] = time.strftime(
                        "%b %d  %H:%M:%S", time.localtime(float(ts)))
                except Exception:
                    row["ts_human"] = ""
                recent.append(row)
        except Exception:
            recent = []

    return {
        "enabled": enabled,
        "prefix": prefix,
        "email_url": email_url,
        "apps": apps,
        "supported": _va.list_supported(cfg),
        "recent": recent,
    }
