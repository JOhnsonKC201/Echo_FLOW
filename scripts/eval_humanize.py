"""Offline quality benchmark for the paste-in humanizer (Cleaner.humanize_text).

Unlike scripts/eval_intent.py, this one needs a running Ollama — it measures the
real model's rewrites, which is the only honest way to track de-AI quality. So
it is a DEV / release harness, not a CI gate (the deterministic tell detector it
scores with, src/aitells.py, IS unit-tested in CI).

It runs a fixed corpus of AI-written passages through the humanizer and reports:
  - accepted    : produced a rewrite (reason ok|warned), not just kept-original
  - tells-removed: 1 - aitells.score(after) / aitells.score(before), averaged
  - facts-kept  : numbers present in the source still present in the output
  - contaminated: output leaked writing-sample content (voice mode; must be 0)
  - median latency

Hard-exclude is ON here (the production default): sentences carrying numbers,
citations, quotes or code are kept byte-for-byte and never sent to the model, so
facts-kept is ~100% while tell-removal is capped (their tells survive on purpose).

`--check` fails (non-zero exit) if acceptance or tell-removal fall below the
bars, so a prompt/guard regression is caught before release.

Run:
  python scripts/eval_humanize.py                    # full report
  python scripts/eval_humanize.py --model qwen3.5:latest
  python scripts/eval_humanize.py --reps 3           # average over N runs
  python scripts/eval_humanize.py --check            # release gate
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.cleanup import Cleaner            # noqa: E402
from src import aitells                    # noqa: E402


CFG = {
    "enabled": True, "provider": "ollama",
    "ollama": {"base_url": "http://localhost:11434",
               "model": "qwen2.5:3b-instruct-q4_K_M",
               "timeout_sec": 8.0, "keep_alive": "20m"},
}

# Writing samples for the one voice-mode case — deliberately off-topic so any
# leak into the output is obvious.
PROFILE = ("WRITING SAMPLES (how you actually write):\n"
           "Spent the weekend on GPU passthrough. Turns out the driver was fine "
           "the whole time. Two days for a settings toggle. Anyway, it runs now.")

# (name, text, facts, mode, tone) — AI-written passages with known figures.
CASES = [
    ("announcement",
     "It's important to note that this new architecture represents a testament "
     "to our team's robust engineering culture. Moreover, by leveraging a "
     "seamless integration layer, we navigate the evolving landscape of user "
     "needs and unlock unprecedented value.",
     [], "human", ""),
    ("with-numbers",
     "In today's ever-evolving digital landscape, our Q3 results underscore a "
     "pivotal shift. Revenue grew 42% to $18.5 million, while churn fell to "
     "3.2%. These metrics are a testament to the robust strategy the team has "
     "harnessed.",
     ["42", "18.5", "3.2"], "human", ""),
    ("technical",
     "Delving into the realm of distributed systems, it becomes crucial to "
     "understand that consensus is not merely a technical challenge. Raft, "
     "introduced in 2014, offers a myriad of advantages over Paxos, fostering "
     "greater understandability while maintaining robust safety guarantees.",
     ["2014"], "human", ""),
    ("email",
     "I wanted to reach out and touch base regarding the pivotal project. Moving "
     "forward, it's important to note that we should leverage our synergies to "
     "foster a more holistic approach. Please don't hesitate to reach out.",
     [], "tone", "casual"),
    ("three-paragraph",
     "The team has been navigating a complex landscape this quarter. Moreover, "
     "the challenges have been myriad.\n\n"
     "It's crucial to note that our robust testing framework caught 14 "
     "regressions before release. This is a testament to the process.\n\n"
     "Furthermore, we will continue to leverage these learnings to foster a "
     "culture of excellence.",
     ["14"], "human", ""),
    ("voice-mode",
     "It's important to note that this represents a testament to our robust "
     "culture. Moreover, we leverage seamless synergies to navigate the "
     "evolving landscape.",
     [], "voice", ""),
]

PROFILE_MARKERS = ["gpu", "passthrough", "driver", "toggle", "settings"]


def _facts_kept(facts, out):
    if not facts:
        return 1.0
    return sum(1 for f in facts if f in out) / len(facts)


def _contaminated(out):
    low = out.lower()
    return any(m in low for m in PROFILE_MARKERS)


def run(model: str, reps: int, timeout: float, verbose: bool):
    cleaner = Cleaner(CFG)
    rows = []
    for name, text, facts, mode, tone in CASES:
        base = aitells.score(text)
        for _ in range(reps):
            t0 = time.time()
            o = cleaner.humanize_text(
                text, mode=mode, tone=tone,
                voice_profile=PROFILE if mode == "voice" else "",
                retriever=None, timeout_sec=timeout, model=model,
                escalate_model="",     # measure the target model alone
                protect_spans=True)    # production default: hard-exclude on
            el = time.time() - t0
            after = aitells.score(o.text) if o.text else base
            accepted = o.reason in ("ok", "warned")
            removed = (1 - after / base) if base and accepted else (
                1.0 if accepted and base == 0 else 0.0)
            rows.append({
                "case": name, "reason": o.reason, "sec": el,
                "removed": max(0.0, removed),
                "facts": _facts_kept(facts, o.text or ""),
                "contaminated": mode == "voice" and o.text and _contaminated(o.text),
                "before": base, "after": after,
            })
            if verbose:
                print(f"  {name:16s} {o.reason:8s} {el:4.1f}s  "
                      f"tells {base}->{after}  facts={rows[-1]['facts']:.0%}")
    return rows


def summarize(rows):
    n = len(rows)
    accepted = [r for r in rows if r["reason"] in ("ok", "warned")]
    acc = len(accepted) / n
    removed = statistics.mean(r["removed"] for r in rows)
    facts = statistics.mean(r["facts"] for r in rows)
    contaminated = sum(1 for r in rows if r["contaminated"])
    print("=" * 60)
    print(f"  runs               : {n}")
    print(f"  accepted           : {acc:.0%}")
    print(f"  tells removed (avg): {removed:.0%}")
    print(f"  facts kept (avg)   : {facts:.0%}")
    print(f"  contaminated       : {contaminated}  (must be 0)")
    print(f"  median latency     : {statistics.median(r['sec'] for r in rows):.1f}s")
    return {"accept": acc, "removed": removed, "facts": facts,
            "contaminated": contaminated}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="", help="Ollama model (blank = config default)")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--check", action="store_true", help="fail if below the bars")
    args = ap.parse_args()

    print(f"model: {args.model or CFG['ollama']['model']}   reps: {args.reps}")
    rows = run(args.model, args.reps, args.timeout, verbose=True)
    m = summarize(rows)

    if args.check:
        # Bars chosen from the observed baseline on qwen2.5:3b. Note that
        # hard-exclude is ON (production default): a sentence carrying a number,
        # citation or code is kept verbatim, so its tells survive by design.
        # That caps tell-removal (~64% observed vs a protection-off ceiling) but
        # guarantees facts-kept ~100% — the tradeoff the tool deliberately makes.
        ACCEPT_MIN, REMOVED_MIN, FACTS_MIN = 0.70, 0.60, 0.85
        ok = (m["accept"] >= ACCEPT_MIN and m["removed"] >= REMOVED_MIN
              and m["facts"] >= FACTS_MIN and m["contaminated"] == 0)
        print("\nCHECK:", "PASS" if ok else "FAIL",
              f"(accept>={ACCEPT_MIN:.0%} removed>={REMOVED_MIN:.0%} "
              f"facts>={FACTS_MIN:.0%} contaminated==0)")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
