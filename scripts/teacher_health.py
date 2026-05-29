"""Teacher-loop health snapshot — passive observability for the distillation layer.

Reads data/history.db and prints:
  - dictation counts by source (desktop / mobile / teacher)
  - teacher acceptance ratio (teacher rows / desktop rows since teacher-loop start)
  - learned_patterns origin breakdown (user_count vs teacher_count)
  - confident-pattern count (passes the >=0.7 / >=2 gate the `learned` provider uses)
  - current phase decision (independent vs self_sufficient) with the gating numbers
  - last 5 teacher rows for spot-checking

Usage (from repo root):
    .venv\\Scripts\\python.exe scripts\\teacher_health.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

# Windows consoles default to CP1252 which can't encode m-dashes or arrows
# emitted by phase.decide().reason ("→ raw local Whisper output"). Switch
# stdout to UTF-8 so the script doesn't crash with UnicodeEncodeError.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402

from src import phase as phase_mod  # noqa: E402


def _load_cfg() -> dict:
    with open(PROJECT_ROOT / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _open(db_path: str) -> sqlite3.Connection | None:
    if not os.path.exists(db_path):
        print(f"history db not found at {db_path}")
        return None
    return sqlite3.connect(db_path)


def _source_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT COALESCE(source,'desktop') AS s, COUNT(*) FROM dictations GROUP BY s"
    ).fetchall()
    return {s: int(n) for s, n in rows}


def _pattern_origin(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """Return (total_patterns, total_user_observations, total_teacher_observations)."""
    try:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(user_count),0), COALESCE(SUM(teacher_count),0) "
            "FROM learned_patterns"
        ).fetchone()
    except sqlite3.OperationalError:
        return 0, 0, 0
    return int(row[0]), int(row[1]), int(row[2])


def _confident_count(conn: sqlite3.Connection, min_conf: float = 0.7, min_total: int = 2) -> int:
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM learned_patterns "
            "WHERE total >= ? AND (success * 1.0 / total) >= ?",
            (min_total, min_conf),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0])


def _recent_teacher_rows(conn: sqlite3.Connection, n: int = 5):
    try:
        return conn.execute(
            "SELECT ts, length(raw_text), length(cleaned_text), "
            "       COALESCE(quality_score, -1), substr(cleaned_text, 1, 60) "
            "FROM dictations WHERE source='teacher' ORDER BY ts DESC LIMIT ?",
            (n,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def main() -> int:
    cfg = _load_cfg()
    db_path = str(PROJECT_ROOT / cfg.get("history", {}).get("db_path", "data/history.db"))
    conn = _open(db_path)
    if conn is None:
        return 1

    print(f"# Teacher health — {db_path}")
    print(f"# {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    by_source = _source_counts(conn)
    desktop = by_source.get("desktop", 0)
    mobile = by_source.get("mobile", 0)
    teacher = by_source.get("teacher", 0)
    grand = sum(by_source.values())
    print("## Dictation sources")
    for s in ("desktop", "mobile", "teacher"):
        n = by_source.get(s, 0)
        pct = (100.0 * n / grand) if grand else 0.0
        print(f"  {s:<9} {n:>7}  ({pct:5.1f}%)")
    print(f"  {'total':<9} {grand:>7}\n")

    teach_cfg = cfg.get("cleanup", {}).get("learning", {}) or {}
    teach_on = bool(teach_cfg.get("teacher_enabled", False))
    sample = float(teach_cfg.get("teacher_sample_rate", 1.0))
    gate = bool(teach_cfg.get("teacher_quality_gate", True))
    print("## Teacher loop config")
    print(f"  enabled        {teach_on}")
    print(f"  sample_rate    {sample}")
    print(f"  quality_gate   {gate}")
    print(f"  model_override {teach_cfg.get('teacher_model') or '(use cleanup.groq.model)'}")
    print(f"  trust_teacher  {teach_cfg.get('trust_teacher', True)}\n")

    # Acceptance ratio: teacher rows / non-teacher rows that occurred during the
    # window when teacher mode could have fired. We can't recover historical
    # toggles, so this is a coarse lifetime ratio — useful for "is the loop
    # running at all?", not a precise gate-acceptance number.
    base = desktop + mobile
    ratio = (teacher / base) if base else 0.0
    print("## Teacher acceptance (lifetime, coarse)")
    print(f"  teacher_rows / (desktop+mobile) = {teacher} / {base} = {ratio:.3f}")
    if teach_on and base >= 50 and ratio < 0.05:
        print("  NOTE: teacher is enabled but acceptance < 5%. Check:")
        print("        - GROQ_API_KEY set in the daemon env?")
        print("        - teacher_sample_rate or teacher_min_chars too strict?")
        print("        - quality_gate dropping teacher outputs as worse than yours?\n")
    else:
        print()

    total_pat, user_obs, teach_obs = _pattern_origin(conn)
    confident = _confident_count(conn)
    print("## learned_patterns")
    print(f"  total rows               {total_pat}")
    print(f"  user observations        {user_obs}")
    print(f"  teacher observations     {teach_obs}")
    print(f"  confident (>=0.7, n>=2)  {confident}\n")

    # Live phase decision, same logic the daemon uses on startup.
    decision = phase_mod.decide(cfg, db_path)
    self_suff_after = int(cfg.get("phasing", {}).get("self_sufficient_after", 2000))
    progress = min(100.0, 100.0 * (desktop + mobile) / self_suff_after) if self_suff_after else 0.0
    print("## Phase decision")
    print(f"  name              {decision.name}")
    print(f"  cleanup_provider  {decision.cleanup_provider}")
    print(f"  reason            {decision.reason}")
    print(f"  progress to self_sufficient  {progress:5.1f}%  ({desktop+mobile}/{self_suff_after})\n")

    rows = _recent_teacher_rows(conn, n=5)
    print("## Most recent teacher rows")
    if not rows:
        print("  (none)")
    else:
        for ts, raw_len, clean_len, q, preview in rows:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
            q_str = f"q={q:.0f}" if q >= 0 else "q=?"
            print(f"  {when}  raw={raw_len:<4} clean={clean_len:<4} {q_str:<6} {preview!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
