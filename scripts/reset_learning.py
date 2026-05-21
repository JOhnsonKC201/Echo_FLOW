"""Clean out poisoned dictation history.

The system was returning chatbot-style "audit reports" instead of cleaning,
and those bad outputs got stored as training examples. The RAG retriever
then fed those back to the model as few-shot examples, reinforcing the
bad behavior.

This script deletes obviously poisoned rows: ones where cleaned_text
contains markdown structure, audit-report patterns, or is dramatically
longer than raw_text.

Run:  python reset_learning.py            # dry-run, show what would be deleted
      python reset_learning.py --apply    # actually delete
      python reset_learning.py --wipe     # delete EVERYTHING (clean slate)
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


DB = "data/history.db"

POISONED_SIGNALS = (
    "**", "##", "Cleaned Text:", "Filler Words:", "Grammar and Punctuation:",
    "Language Detection:", "Vocabulary Check:", "Edge Cases:",
    "System Audit", "Pass:", "Fail:", "Warning:",
    "Preserved Vocabulary:", "Error Check:", "Remove Duplicate",
    "Semantic Similarity Check:",
)

# Chatbot phrases that should NEVER appear in a transcript cleanup
CHATBOT_PHRASES = (
    "I'm fine", "I am fine", "I'm doing", "I am doing well",
    "Thanks for asking", "thanks for asking", "I can help",
    "I'd be happy", "I would be happy", "I'm glad you",
    "Let me know if", "Please let me know",
    "Here's a", "Here is a", "Here is the",
    "I'm an AI", "I am an AI", "As an AI",
)


def find_poisoned(conn) -> list[tuple[int, str, str]]:
    rows = conn.execute(
        "SELECT id, raw_text, cleaned_text FROM dictations"
    ).fetchall()
    bad = []
    for rid, raw, cleaned in rows:
        if not cleaned:
            continue
        raw_len = max(len(raw or ""), 1)
        # Length explosion: real cleanup is ≤1.8x input
        if len(cleaned) > max(60, raw_len * 1.8):
            bad.append((rid, raw, cleaned))
            continue
        # Length collapse: <40% means truncation or chatbot one-liner reply
        if len(cleaned) < raw_len * 0.4 and raw_len > 20:
            bad.append((rid, raw, cleaned))
            continue
        # Markdown / structured-report signals
        if any(sig in cleaned for sig in POISONED_SIGNALS):
            bad.append((rid, raw, cleaned))
            continue
        # Chatbot phrases — these should never appear in a transcript cleanup
        if any(p in cleaned for p in CHATBOT_PHRASES):
            # But allow if the user actually said something similar
            if not any(p.lower() in (raw or "").lower() for p in CHATBOT_PHRASES):
                bad.append((rid, raw, cleaned))
                continue
        # Too many newlines = structured response
        if cleaned.count("\n") > (raw or "").count("\n") + 2:
            bad.append((rid, raw, cleaned))
    return bad


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="actually delete poisoned rows")
    p.add_argument("--wipe", action="store_true", help="delete EVERYTHING")
    args = p.parse_args()

    if not Path(DB).exists():
        print(f"No DB at {DB}. Nothing to do.")
        return 0

    conn = sqlite3.connect(DB)
    total = conn.execute("SELECT COUNT(*) FROM dictations").fetchone()[0]
    print(f"Total dictations: {total}")

    if args.wipe:
        print(f"WIPE mode — deleting all {total} rows.")
        if input("Type YES to confirm: ") == "YES":
            conn.execute("DELETE FROM dictations")
            conn.commit()
            conn.execute("VACUUM")
            print("[OK] Wiped clean. Start dictating fresh.")
        else:
            print("Cancelled.")
        return 0

    bad = find_poisoned(conn)
    print(f"Poisoned rows detected: {len(bad)} / {total}")
    if not bad:
        print("Nothing to clean.")
        return 0

    print("\nFirst 5 examples of what would be deleted:")
    for rid, raw, cleaned in bad[:5]:
        print(f"\n  #{rid}")
        print(f"  RAW:     {(raw or '')[:100]}")
        print(f"  CLEANED: {cleaned[:200]}{'…' if len(cleaned) > 200 else ''}")

    if not args.apply:
        print(f"\nDry run. Re-run with --apply to actually delete these {len(bad)} rows.")
        return 0

    ids = [rid for rid, _, _ in bad]
    conn.executemany("DELETE FROM dictations WHERE id = ?", [(i,) for i in ids])
    conn.commit()
    conn.execute("VACUUM")
    remaining = conn.execute("SELECT COUNT(*) FROM dictations").fetchone()[0]
    print(f"\n[OK] Deleted {len(ids)} poisoned rows. {remaining} clean dictations remain.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
