"""Backfill teacher-distilled cleanups from existing dictation history.

The teacher-model learning loop (cleanup.learning.teacher_enabled) only
runs forward — every NEW dictation gets a teacher re-cleanup. This script
bootstraps the loop from your past dictations: it walks existing rows
where source='desktop', calls the teacher (Groq) on each raw_text, and
inserts the teacher's output as a new row with source='teacher'.
PatternMiner then learns from both sides.

Run:
  python scripts/backfill_teacher.py                 # dry-run, prints what it would do
  python scripts/backfill_teacher.py --apply         # actually do it
  python scripts/backfill_teacher.py --apply --limit 100
  python scripts/backfill_teacher.py --apply --style default

Needs GROQ_API_KEY in the environment and the daemon NOT running (we open
the same SQLite file directly).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `src.*` importable when run as `python scripts/backfill_teacher.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

from src.cleanup import Cleaner
from src.history import History
from src.learn import PatternMiner


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="Actually write teacher rows. Default is dry-run.")
    ap.add_argument("--limit", type=int, default=200,
                    help="Max dictations to teach (default 200).")
    ap.add_argument("--style", default=None,
                    help="Only teach this style (default: all non-prompt).")
    ap.add_argument("--config", default="config.yaml",
                    help="Path to config.yaml.")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"config not found: {cfg_path}", file=sys.stderr)
        return 2
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    db_path = ((cfg.get("history") or {}).get("db_path")) or "data/history.db"

    # Force teacher_enabled on for this run — the gate is the daemon's,
    # the backfill is opt-in by virtue of running this script.
    cleanup_cfg = dict(cfg.get("cleanup") or {})
    learning_cfg = dict(cleanup_cfg.get("learning") or {})
    learning_cfg["teacher_enabled"] = True
    cleanup_cfg["learning"] = learning_cfg
    cleaner = Cleaner(cleanup_cfg)

    h = History(db_path)
    miner = PatternMiner(db_path)

    where = "source = 'desktop' AND style != 'prompt' AND raw_text != cleaned_text"
    params: list = []
    if args.style:
        where += " AND style = ?"
        params.append(args.style)
    where += " ORDER BY ts DESC LIMIT ?"
    params.append(args.limit)

    rows = h.conn.execute(
        f"SELECT id, raw_text, style, language, duration_ms, window_title "
        f"FROM dictations WHERE {where}",
        params,
    ).fetchall()

    print(f"backfill: {len(rows)} candidate row(s) (apply={args.apply})")
    written = 0
    for rid, raw, style, lang, dur, wt in rows:
        if not raw or not raw.strip():
            continue
        try:
            teacher_out = cleaner.teach(raw, style=style or "default")
        except Exception as e:
            print(f"  #{rid}: teach failed ({e})")
            continue
        if not teacher_out:
            print(f"  #{rid}: no teacher output")
            continue
        print(f"  #{rid} [{style}]:")
        print(f"    raw     : {raw[:120]}")
        print(f"    teacher : {teacher_out[:120]}")
        if args.apply:
            try:
                h.log(
                    window_title=wt or "",
                    style=style or "default",
                    language=lang or "en",
                    duration_ms=int(dur or 0),
                    raw_text=raw,
                    cleaned_text=teacher_out,
                    source="teacher",
                )
                miner.record(raw, teacher_out)
                written += 1
            except Exception as e:
                print(f"  #{rid}: write failed ({e})")

    print(f"backfill: done. wrote {written} teacher row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
