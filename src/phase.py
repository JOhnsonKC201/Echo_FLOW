"""Auto-phasing (local-only).

Echo Flow is local-only — there is no cloud bootstrap. Phases now describe
which local cleanup path is used:

  Phase 1 — Independent     (0 – self_sufficient_after): Local Whisper + Ollama
  Phase 2 — Self-Sufficient (>= self_sufficient_after, with enough learned
                             patterns + quality): Local Whisper + learned

If Ollama is unreachable, we degrade to raw Whisper output (`none`).
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

import requests

from . import log as wlog

_log = wlog.get("phase")


PHASE_INDEPENDENT = "independent"
PHASE_SELF_SUFFICIENT = "self_sufficient"


@dataclass
class PhaseDecision:
    name: str
    transcribe_backend: str   # always "local"
    cleanup_provider: str     # "ollama" | "learned" | "none"
    reason: str


def _dictation_count(db_path: str) -> int:
    try:
        if not os.path.exists(db_path):
            return 0
        conn = sqlite3.connect(db_path)
        cur = conn.execute("SELECT COUNT(*) FROM dictations")
        return int(cur.fetchone()[0])
    except Exception as e:
        _log.exception(f"Suppressed in _dictation_count: {e}")
        return 0


def _ollama_alive(base_url: str) -> bool:
    try:
        r = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=1.0)
        return r.status_code == 200
    except Exception as e:
        _log.exception(f"Suppressed in _ollama_alive: {e}")
        return False


def _recent_avg_quality(db_path: str, n: int = 50) -> float | None:
    try:
        if not os.path.exists(db_path):
            return None
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='dictations'"
        )
        if int(cur.fetchone()[0]) == 0:
            return None
        rows = conn.execute(
            "SELECT quality_score FROM dictations "
            "WHERE quality_score IS NOT NULL ORDER BY ts DESC LIMIT ?",
            (n,),
        ).fetchall()
        if len(rows) < max(5, n // 4):
            return None
        return sum(r[0] for r in rows) / len(rows)
    except Exception as e:
        _log.exception(f"Suppressed in _recent_avg_quality: {e}")
        return None


def _learned_pattern_count(db_path: str) -> int:
    try:
        if not os.path.exists(db_path):
            return 0
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='learned_patterns'"
        )
        if int(cur.fetchone()[0]) == 0:
            return 0
        cur = conn.execute(
            "SELECT COUNT(*) FROM learned_patterns WHERE total >= 2 AND (success * 1.0 / total) >= 0.7"
        )
        return int(cur.fetchone()[0])
    except Exception as e:
        _log.exception(f"Suppressed in _learned_pattern_count: {e}")
        return 0


def decide(cfg: dict, db_path: str) -> PhaseDecision:
    """Pick the right phase based on history + available local services.

    Always returns transcribe_backend='local'. Cloud provider names in config
    are ignored — they are normalized to a local provider here.
    """
    pcfg = cfg.get("phasing", {})
    if not pcfg.get("enabled", True):
        # Respect manual config but force local-only.
        provider = cfg.get("cleanup", {}).get("provider", "ollama")
        if provider in ("groq", "anthropic", "openai"):
            _log.warning(
                "cleanup.provider=%s is a legacy cloud provider; using ollama.",
                provider,
            )
            provider = "ollama"
        return PhaseDecision(
            name="manual",
            transcribe_backend="local",
            cleanup_provider=provider,
            reason="phasing disabled in config",
        )

    n = _dictation_count(db_path)
    self_sufficient_after = pcfg.get("self_sufficient_after", 2000)
    ollama_url = cfg.get("cleanup", {}).get("ollama", {}).get(
        "base_url", "http://localhost:11434"
    )
    ollama_ok = _ollama_alive(ollama_url)

    # Phase 2 — Self-Sufficient: LLM-free cleanup once enough patterns AND quality exist.
    if n >= self_sufficient_after:
        pattern_count = _learned_pattern_count(db_path)
        avg_q = _recent_avg_quality(db_path, n=50)
        quality_ok = (avg_q is not None) and (avg_q >= 75.0)
        patterns_ok = pattern_count >= max(50, self_sufficient_after // 20)
        if patterns_ok and quality_ok:
            return PhaseDecision(
                PHASE_SELF_SUFFICIENT, "local", "learned",
                f"{n} dictations + {pattern_count} patterns + avg quality {avg_q:.0f} → LLM-free",
            )

    # Phase 1 — Independent (local Whisper + Ollama)
    if ollama_ok:
        return PhaseDecision(
            PHASE_INDEPENDENT, "local", "ollama",
            f"{n} dictations → local Whisper + Ollama",
        )
    return PhaseDecision(
        PHASE_INDEPENDENT, "local", "none",
        "Ollama unreachable → raw local Whisper output",
    )
