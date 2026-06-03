"""Backfill: collapse repeated phrases in existing dictation cleaned_text.

New dictations are de-duplicated by the cleanup pipeline (collapse_repeats runs
inside _polish_text). This one-time script fixes rows already stored before
that change — e.g. "Open Browser Open Browser" → "Open Browser".

Only `cleaned_text` is touched. `original_cleaned` (the model's audit copy) and
embeddings (derived from raw_text, not cleaned_text) are left untouched, so the
graph/search stay valid.

Run:
  python scripts/backfill_collapse.py                 # dry-run, prints diffs
  python scripts/backfill_collapse.py --apply         # write the fixes
  python scripts/backfill_collapse.py --apply --limit 500
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `src.*` importable when run as `python scripts/backfill_collapse.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

from src.cleanup import collapse_repeats
from src.history import History


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="Actually write the collapsed text. Default is dry-run.")
    ap.add_argument("--limit", type=int, default=100000,
                    help="Max rows to scan (default: effectively all).")
    ap.add_argument("--config", default="config.yaml",
                    help="Path to config.yaml.")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"config not found: {cfg_path}", file=sys.stderr)
        return 2
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    db_path = ((cfg.get("history") or {}).get("db_path")) or "data/history.db"

    h = History(db_path)
    rows = h.conn.execute(
        "SELECT id, cleaned_text FROM dictations "
        "WHERE style != 'prompt' AND cleaned_text IS NOT NULL AND cleaned_text != '' "
        "ORDER BY ts DESC LIMIT ?",
        (args.limit,),
    ).fetchall()

    print(f"backfill-collapse: scanning {len(rows)} row(s) (apply={args.apply})")
    changed = 0
    for rid, cleaned in rows:
        new = collapse_repeats(cleaned)
        if new == cleaned:
            continue
        changed += 1
        print(f"  #{rid}:")
        print(f"    before: {cleaned[:140]}")
        print(f"    after : {new[:140]}")
        if args.apply:
            h.conn.execute(
                "UPDATE dictations SET cleaned_text = ? WHERE id = ?",
                (new, rid),
            )

    if args.apply:
        h.conn.commit()
        print(f"backfill-collapse: done. updated {changed} row(s).")
    else:
        print(f"backfill-collapse: dry-run. {changed} row(s) would change. "
              f"Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
