"""Page-data helper for the top-level /actions section (Phase 14 Action Mode).

Read-only view over voice_actions.list_supported() + the configured
action_apps map + the history.voice_actions log populated by main.py's
dispatch site. Mirrors commands_view.page_data.
"""
from __future__ import annotations

import json
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
    require_prefix = bool(exp.get("action_require_prefix", True))
    prefix = exp.get("command_prefix", "computer")
    email_url = exp.get("action_email_url", "https://mail.google.com")

    # Merge config defaults with dashboard-managed (SQLite) shortcuts. User rows
    # override config by name and are the only ones the editor can remove; config
    # rows show as read-only defaults.
    def _rows(kind: str, cfg_key: str) -> list[dict]:
        cfg_map = {str(k).strip().lower(): str(v)
                   for k, v in (exp.get(cfg_key, {}) or {}).items()}
        user_map: dict[str, str] = {}
        if history is not None and getattr(history, "conn", None) is not None:
            try:
                for r in history.list_action_targets(kind):
                    user_map[str(r["name"]).strip().lower()] = str(r["target"])
            except Exception:
                user_map = {}
        out = []
        for name in sorted(set(cfg_map) | set(user_map)):
            is_user = name in user_map
            out.append({
                "name": name,
                "target": user_map.get(name, cfg_map.get(name, "")),
                "source": "user" if is_user else "config",
                # A user row that shadows a config default reverts (not deletes)
                # back to the config value when removed.
                "shadows": is_user and name in cfg_map,
            })
        return out

    apps = _rows("app", "action_apps")
    folders = _rows("folder", "action_folders")

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
                # MODEL-SHADOW enrichment: parse the persisted prediction so the
                # template can render shadow rows ("would have fired X") and the
                # agreement marker on executed rows without touching JSON.
                row["is_shadow"] = row.get("handler") == "intent_shadow"
                model = None
                if row.get("model_pred"):
                    try:
                        parsed = json.loads(row["model_pred"])
                        if isinstance(parsed, dict):
                            model = parsed
                    except (TypeError, ValueError):
                        model = None
                row["model"] = model
                recent.append(row)
        except Exception:
            recent = []

    # MODEL-SHADOW summary: online agreement/recovery stats while the intent
    # model is enabled (shadow or live). None hides the block entirely.
    intent = None
    im_mode = exp.get("action_intent_model")
    if im_mode and history is not None and getattr(history, "conn", None) is not None:
        try:
            intent = {"mode": "shadow" if im_mode == "shadow" else "on",
                      **history.intent_agreement_stats()}
        except Exception:
            intent = None

    return {
        "enabled": enabled,
        "require_prefix": require_prefix,
        "prefix": prefix,
        "email_url": email_url,
        "apps": apps,
        "folders": folders,
        "supported": _va.list_supported(cfg),
        "recent": recent,
        "intent": intent,
    }
