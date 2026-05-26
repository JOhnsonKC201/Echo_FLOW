"""Page-data helper for the top-level /commands section.

Reads from the existing src.commands allowlist + history.command_log
table populated by main.py's dispatch site.
"""
from __future__ import annotations

import time
from typing import Any


def page_data(cfg: dict, history) -> dict:
    """Build the payload for templates/commands.html.

    Returns:
        {
          "enabled": bool,
          "prefix": str,
          "supported": list[str],
          "recent": list[dict],   # most recent command_log entries
        }
    """
    from .. import commands as _cmds
    exp = (cfg or {}).get("experimental", {}) or {}
    enabled = bool(exp.get("command_mode", False))
    prefix = exp.get("command_prefix", "computer")
    recent: list[dict] = []
    if history is not None and getattr(history, "conn", None) is not None:
        try:
            for row in history.recent_commands(limit=30):
                ts = row.get("ts") or 0
                try:
                    row["ts_human"] = time.strftime("%b %d  %H:%M:%S", time.localtime(float(ts)))
                except Exception:
                    row["ts_human"] = ""
                recent.append(row)
        except Exception:
            recent = []
    return {
        "enabled": enabled,
        "prefix": prefix,
        "supported": _cmds.list_supported(),
        "recent": recent,
    }
