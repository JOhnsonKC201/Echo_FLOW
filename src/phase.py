"""Auto-phasing: gradually weans off Groq as your history grows.

Phase 1 — Bootstrap   (0   – 50 dictations): Groq Whisper + Groq Llama
Phase 2 — Hybrid      (50  – 200 dictations): Local Whisper + Groq Llama
Phase 3 — Independent (200+ dictations):       Local Whisper + Ollama (if available)

The transition is gradual and reversible:
  - If Groq is unreachable mid-phase, we fall back gracefully
  - If Ollama isn't installed in Phase 3, we stay on Groq cleanup
  - Phase is recomputed on every dictation so it adapts in real time
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

import requests


PHASE_BOOTSTRAP = "bootstrap"
PHASE_HYBRID = "hybrid"
PHASE_INDEPENDENT = "independent"
PHASE_SELF_SUFFICIENT = "self_sufficient"


@dataclass
class PhaseDecision:
    name: str
    transcribe_backend: str   # "groq" | "local"
    cleanup_provider: str     # "groq" | "ollama" | "none"
    reason: str


def _dictation_count(db_path: str) -> int:
    try:
        if not os.path.exists(db_path):
            return 0
        conn = sqlite3.connect(db_path)
        cur = conn.execute("SELECT COUNT(*) FROM dictations")
        return int(cur.fetchone()[0])
    except Exception:
        return 0


def _ollama_alive(base_url: str) -> bool:
    try:
        r = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=1.0)
        return r.status_code == 200
    except Exception:
        return False


def _groq_key_set() -> bool:
    return bool(os.environ.get("GROQ_API_KEY"))


def _recent_avg_quality(db_path: str, n: int = 50) -> float | None:
    """Mean quality_score across the last n dictations, or None if not enough."""
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
    except Exception:
        return None


def _learned_pattern_count(db_path: str) -> int:
    """How many confident learned patterns currently exist."""
    try:
        if not os.path.exists(db_path):
            return 0
        conn = sqlite3.connect(db_path)
        # Table may not exist yet on first run.
        cur = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='learned_patterns'"
        )
        if int(cur.fetchone()[0]) == 0:
            return 0
        cur = conn.execute(
            "SELECT COUNT(*) FROM learned_patterns WHERE total >= 2 AND (success * 1.0 / total) >= 0.7"
        )
        return int(cur.fetchone()[0])
    except Exception:
        return 0


def decide(cfg: dict, db_path: str) -> PhaseDecision:
    """Pick the right phase based on history + available services."""
    pcfg = cfg.get("phasing", {})
    if not pcfg.get("enabled", True):
        # Respect manual config
        return PhaseDecision(
            name="manual",
            transcribe_backend=cfg["whisper"].get("backend", "hybrid"),
            cleanup_provider=cfg["cleanup"].get("provider", "groq"),
            reason="phasing disabled in config",
        )

    n = _dictation_count(db_path)
    bootstrap_until = pcfg.get("bootstrap_until", 50)
    independent_after = pcfg.get("independent_after", 200)
    self_sufficient_after = pcfg.get("self_sufficient_after", 2000)
    groq_ok = _groq_key_set()
    ollama_url = cfg.get("cleanup", {}).get("ollama", {}).get(
        "base_url", "http://localhost:11434"
    )
    ollama_ok = _ollama_alive(ollama_url)

    # Phase 4 — Self-Sufficient: LLM-free cleanup once enough patterns AND quality exist.
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
        # Not enough patterns or quality yet — keep using Ollama or Groq.
        if ollama_ok:
            why = []
            if not patterns_ok: why.append(f"patterns={pattern_count}")
            if not quality_ok: why.append(f"quality={avg_q}")
            return PhaseDecision(
                PHASE_INDEPENDENT, "local", "ollama",
                f"{n} dictations but {', '.join(why)} → keep Ollama",
            )

    # Phase 3 — Independent
    if n >= independent_after:
        if ollama_ok:
            return PhaseDecision(
                PHASE_INDEPENDENT, "local", "ollama",
                f"{n} dictations + Ollama detected → fully offline",
            )
        if groq_ok:
            return PhaseDecision(
                PHASE_HYBRID, "local", "groq",
                f"{n} dictations but Ollama missing → stay on Groq cleanup",
            )
        return PhaseDecision(
            PHASE_INDEPENDENT, "local", "none",
            f"{n} dictations, no Ollama, no Groq → raw Whisper output",
        )

    # Phase 2 — Hybrid
    if n >= bootstrap_until:
        if groq_ok:
            return PhaseDecision(
                PHASE_HYBRID, "local", "groq",
                f"{n} dictations → local Whisper, Groq cleanup",
            )
        if ollama_ok:
            return PhaseDecision(
                PHASE_INDEPENDENT, "local", "ollama",
                f"{n} dictations, no Groq → Ollama cleanup",
            )
        return PhaseDecision(
            PHASE_HYBRID, "local", "none",
            f"{n} dictations, no Groq, no Ollama → raw output",
        )

    # Phase 1 — Bootstrap
    if groq_ok:
        return PhaseDecision(
            PHASE_BOOTSTRAP, "groq", "groq",
            f"{n}/{bootstrap_until} dictations → bootstrapping with Groq",
        )
    if ollama_ok:
        return PhaseDecision(
            PHASE_HYBRID, "local", "ollama",
            f"No Groq key → local Whisper + Ollama",
        )
    return PhaseDecision(
        PHASE_INDEPENDENT, "local", "none",
        "No cloud providers available → raw local Whisper",
    )
