"""Privacy ledger — Echo Flow's biggest non-feature: nothing leaves this box.

This module turns architectural facts about Echo Flow's local-only design
into queryable, auditable signals for the /privacy page. It does not enforce
anything; the daemon's outbound surface is already loopback-only by design.
What this module DOES is surface the truth so the user can verify it
themselves.

Key facts the ledger reports:
  - egress_30d: count of network calls leaving this machine in 30 days.
    Always 0 by construction — Echo Flow's only outbound socket targets
    127.0.0.1:11434 (Ollama). The mobile bridge is loopback unless the
    user explicitly sets mobile.bind_address to a non-loopback address,
    which we flag here as a warning.
  - bridge_state: disabled / loopback / lan (warning).
  - db_size_bytes, audio_size_bytes: real disk usage.
  - last_config_write: mtime(config.yaml).
"""
from __future__ import annotations

import io
import os
import time
import zipfile
from pathlib import Path
from typing import Any


def bridge_state(cfg: dict) -> dict:
    """Describe the mobile bridge's current binding.

    Returns {"state": one of "disabled"|"loopback"|"lan",
             "bind_address": str, "warn": bool}.
    """
    m = (cfg or {}).get("mobile", {}) or {}
    enabled = bool(m.get("enabled", False))
    addr = str(m.get("bind_address", "127.0.0.1") or "127.0.0.1")
    if not enabled:
        return {"state": "disabled", "bind_address": addr, "warn": False}
    is_loopback = addr in ("127.0.0.1", "localhost", "::1")
    if is_loopback:
        return {"state": "loopback", "bind_address": addr, "warn": False}
    return {"state": "lan", "bind_address": addr, "warn": True}


def dir_size_bytes(path: Path) -> int:
    """Recursive size of a directory in bytes. Returns 0 if missing."""
    if not path.exists():
        return 0
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def ledger(cfg: dict, history_db: Path, cfg_path: Path, data_dir: Path) -> dict:
    """Compose the full ledger payload for the /privacy page."""
    db_size = 0
    if history_db.exists():
        try:
            db_size = os.path.getsize(history_db)
        except OSError:
            db_size = 0
    audio_dir = data_dir / "audio"
    audio_size = dir_size_bytes(audio_dir)
    keep_audio = bool((cfg.get("history", {}) or {}).get("keep_audio", False))
    last_cfg_write = None
    try:
        if cfg_path.exists():
            last_cfg_write = os.path.getmtime(cfg_path)
    except OSError:
        last_cfg_write = None
    return {
        # The Big Truth — architectural, not measured. If you want to verify,
        # run Wireshark or `netstat -an | findstr ESTABLISHED` and confirm
        # Echo Flow's PID only talks to 127.0.0.1.
        "egress_30d": 0,
        "egress_provenance": (
            "Architectural fact: Echo Flow only opens sockets to "
            "127.0.0.1 (Ollama, mobile bridge, dashboard). No telemetry, "
            "no cloud sync. Verify with `netstat -an`."
        ),
        "bridge": bridge_state(cfg),
        "ollama_url": ((cfg.get("cleanup", {}) or {}).get("ollama", {}) or {}).get(
            "base_url", "http://localhost:11434"
        ),
        "db_path": str(history_db),
        "db_size_bytes": db_size,
        "audio_size_bytes": audio_size,
        "keep_audio": keep_audio,
        "data_dir": str(data_dir),
        "last_config_write": last_cfg_write,
    }


def humanize_bytes(n: int) -> str:
    if n is None or n <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    val = float(n)
    while val >= 1024 and i < len(units) - 1:
        val /= 1024
        i += 1
    if i == 0:
        return f"{int(val)} {units[i]}"
    return f"{val:.1f} {units[i]}"


def human_age(ts: float | None) -> str:
    if not ts:
        return "never"
    delta = max(0.0, time.time() - float(ts))
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    return f"{int(delta / 86400)}d ago"


def build_export_zip(cfg_path: Path, history_db: Path) -> bytes:
    """Bundle the user's full local state into a zip in memory.

    Includes:
      - config.yaml (if present)
      - history.db (if present)
    Audio is intentionally excluded — it can be huge and the user can
    grab the data folder manually if needed.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if cfg_path.exists():
            zf.write(cfg_path, arcname="config.yaml")
        if history_db.exists():
            zf.write(history_db, arcname="history.db")
        zf.writestr(
            "README.txt",
            "Echo Flow data export\n"
            "=====================\n"
            "This zip is everything Echo Flow stores locally:\n"
            "  - config.yaml: your settings, hotkeys, snippets\n"
            "  - history.db: SQLite log of every dictation\n"
            "Audio recordings (data/audio/) excluded — re-include manually.\n",
        )
    return buf.getvalue()
