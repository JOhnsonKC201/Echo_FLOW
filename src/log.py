"""Structured logging for the dictation daemon.

Writes to data/wispr.log with rotation (5MB × 5 files = 25MB max).
Console output via rich is kept for live use; the file gets everything
INFO+ so post-mortem debugging works when the daemon ran overnight.
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


_FMT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_configured = False


def setup(log_dir: str = "data", level: int = logging.INFO,
          filename: str = "wispr.log") -> None:
    """Initialize root logger. Idempotent.

    ECHOFLOW_LOG_DIR overrides log_dir. The test suite sets it: importing
    src.main calls setup(), and from the repo root "data" is the running
    daemon's own directory, so test fixtures such as "cuda went away" used to
    land in the real wispr.log and read as real failures.
    """
    global _configured
    if _configured:
        return
    log_dir = os.environ.get("ECHOFLOW_LOG_DIR") or log_dir
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_path = Path(log_dir) / filename

    root = logging.getLogger("wispr")
    root.setLevel(level)
    root.handlers.clear()

    # File handler with rotation
    fh = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter(_FMT, _DATEFMT))
    fh.setLevel(level)
    root.addHandler(fh)

    # Stderr handler — WARNING+ only, so terminal stays readable
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    sh.setLevel(logging.WARNING)
    root.addHandler(sh)

    root.propagate = False
    _configured = True


def get(name: str) -> logging.Logger:
    """Get a namespaced logger. Call setup() first."""
    return logging.getLogger(f"wispr.{name}")
