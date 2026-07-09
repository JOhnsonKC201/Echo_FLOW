"""Offline evaluation harness for the local intent model (src/intent_model.py).

The intent model is a regex-miss fallback: it recovers mis-phrased Action Mode
commands ("launch spotify", "play some music") that the anchored classify()
regexes don't catch. Because it can fire real side effects, we never want to
turn it on blind — this harness measures precision/recall on a labeled set so
the confidence floor (`experimental.action_intent_min_conf`) is chosen from
data, not vibes.

The fixture set is deliberately split into:
  - SHOULD-FIRE rows: a mis-phrased command → the handler it must resolve to.
  - SHOULD-ABSTAIN rows: plain dictation (and near-misses whose object isn't a
    configured app / real domain) → must resolve to nothing.

A false positive here is the dangerous case: firing on plain dictation, or
firing the wrong handler. `--check` fails (non-zero exit) if precision or recall
regress below the bars, so this doubles as a CI regression guard.

Run:
  python scripts/eval_intent.py                 # report + min_conf sweep
  python scripts/eval_intent.py --sweep         # just the floor sweep table
  python scripts/eval_intent.py --check         # CI gate: assert the bars hold
  python scripts/eval_intent.py --min-conf 0.8  # score at one floor, verbose
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `src.*` importable when run as `python scripts/eval_intent.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import intent_model as im  # noqa: E402


# Config the model re-validates against — a small allowlist of apps/folders.
CFG = {"experimental": {
    "action_apps": {"spotify": "spotify", "notepad": "notepad.exe"},
    "action_folders": {"downloads": "%USERPROFILE%\\Downloads"},
}}

# (utterance, expected_handler_or_None). None = must abstain (type as normal).
LABELED: "list[tuple[str, str | None]]" = [
    # --- should fire (a mis-phrased command the regex path misses) ----------
    ("launch spotify",                 "open_app"),
    ("fire up notepad",                "open_app"),
    ("start up spotify",               "open_app"),
    ("navigate to github.com",         "open_url"),
    ("take me to docs.python.org",     "open_url"),
    ("visit example.com",              "open_url"),
    ("google best pizza near me",      "web_search"),
    ("look up the weather forecast",   "web_search"),
    ("jot down buy milk and eggs",     "quick_note"),
    ("remember to water the plants",   "quick_note"),
    ("play some music",                "media_key"),
    ("pause the music",                "media_key"),
    ("skip this song",                 "media_key"),
    ("previous track",                 "media_key"),
    ("mute the sound",                 "media_key"),
    ("turn up the volume",             "volume"),
    ("make it quieter",                "volume"),
    ("summarize this",                 "summarize_focused"),
    ("tldr this page",                 "summarize_focused"),
    ("open the clipboard link",        "open_clipboard_link"),
    ("launch spotify.",                "open_app"),      # trailing STT period
    ("navigate to github.com.",        "open_url"),      # trailing STT period
    # --- should abstain (plain dictation + unresolvable near-misses) --------
    ("the meeting went really well today",              None),
    ("i think we should refactor the parser soon",      None),
    ("hello there how are you doing today",             None),
    ("my favorite color is a deep ocean blue",         None),
    ("remind me to call the dentist",                   None),  # below floor
    ("launch the new marketing campaign next quarter",  None),  # not an app
    ("navigate the situation carefully",                None),  # not "navigate to" + domain
    ("play devils advocate for a second",               None),  # not a media command
]


def _predicted(utterance: str, min_conf: float) -> "str | None":
    res = im.infer(utterance, CFG, None, min_conf=min_conf)
    return res.match.name if res.match is not None else None


def score(min_conf: float) -> dict:
    """Return metrics + the per-row outcomes at a given floor."""
    tp = fp = fn = tn = 0
    rows = []
    for utterance, expected in LABELED:
        got = _predicted(utterance, min_conf)
        fired = got is not None
        correct = got == expected
        if expected is not None:            # should fire
            if fired and correct:
                tp += 1
                outcome = "ok"
            else:
                fn += 1
                outcome = "MISS" if not fired else f"WRONG({got})"
        else:                               # should abstain
            if not fired:
                tn += 1
                outcome = "ok"
            else:
                fp += 1
                outcome = f"FALSE-FIRE({got})"
        rows.append((utterance, expected, got, outcome))
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {"min_conf": min_conf, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall, "f1": f1, "rows": rows}


SWEEP = [0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90]


def print_sweep() -> None:
    print("min_conf   precision  recall     f1      fp  fn")
    print("-" * 52)
    best = None
    for mc in SWEEP:
        s = score(mc)
        if best is None or (s["f1"], s["precision"]) > (best["f1"], best["precision"]):
            best = s
        print(f"  {mc:.2f}      {s['precision']:.3f}      {s['recall']:.3f}"
              f"     {s['f1']:.3f}   {s['fp']:>2}  {s['fn']:>2}")
    print("-" * 52)
    print(f"recommended min_conf ~= {best['min_conf']:.2f}  "
          f"(f1={best['f1']:.3f}, precision={best['precision']:.3f}, "
          f"recall={best['recall']:.3f})")
    print(f"current default is {im.DEFAULT_MIN_CONF:.2f}")


def print_report(min_conf: float) -> None:
    s = score(min_conf)
    print(f"Intent-model eval @ min_conf={min_conf:.2f}  "
          f"({len(LABELED)} labeled utterances)\n")
    print(f"  precision {s['precision']:.3f}   recall {s['recall']:.3f}   "
          f"f1 {s['f1']:.3f}")
    print(f"  tp={s['tp']}  fp={s['fp']}  fn={s['fn']}  tn={s['tn']}\n")
    problems = [r for r in s["rows"] if r[3] != "ok"]
    if problems:
        print("  misclassifications:")
        for utt, exp, got, outcome in problems:
            print(f"    [{outcome:>16}] {utt!r}  (expected {exp})")
    else:
        print("  no misclassifications.")
    print()


# Regression bars for --check. The dangerous failure is a false fire, so hold
# precision at a perfect 1.0; allow a hair of recall slack for future fixtures.
PRECISION_BAR = 1.0
RECALL_BAR = 0.95


def main() -> int:
    ap = argparse.ArgumentParser(description="Evaluate the local intent model.")
    ap.add_argument("--check", action="store_true",
                    help="CI gate: exit non-zero if precision/recall regress")
    ap.add_argument("--sweep", action="store_true", help="only the floor sweep")
    ap.add_argument("--min-conf", type=float, default=im.DEFAULT_MIN_CONF,
                    help="floor to score at (default: model default)")
    args = ap.parse_args()

    if args.check:
        s = score(im.DEFAULT_MIN_CONF)
        ok = s["precision"] >= PRECISION_BAR and s["recall"] >= RECALL_BAR
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] precision={s['precision']:.3f} (bar {PRECISION_BAR}) "
              f"recall={s['recall']:.3f} (bar {RECALL_BAR}) @ "
              f"min_conf={im.DEFAULT_MIN_CONF}")
        if not ok:
            print_report(im.DEFAULT_MIN_CONF)
        return 0 if ok else 1

    if args.sweep:
        print_sweep()
        return 0

    print_report(args.min_conf)
    print_sweep()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
