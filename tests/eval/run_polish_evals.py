"""Run the polish-pipeline eval harness.

Usage (from repo root):
    .venv\\Scripts\\python.exe tests\\eval\\run_polish_evals.py

Loads tests/eval/polish_evals.yaml, runs each `raw` input through the same
Cleaner the live app uses (configured via config.yaml's cleanup section),
and prints a per-case table plus a total score.

Scoring per case (max 2 points; +1 extra if expected_exact is provided):
  - contains_all   : 1 point if every needle in expected_contains is in output (case-insensitive)
  - contains_none  : 1 point if no needle in expected_not_contains is in output (case-insensitive)
  - exact          : 1 bonus point if output == any element of expected_exact

The harness uses the cleanup section of config.yaml verbatim and forces the
provider to 'ollama' (this script is for measuring the local polish path).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.cleanup import Cleaner  # noqa: E402


def _load_cfg() -> dict:
    with open(PROJECT_ROOT / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _icontains(haystack: str, needle: str) -> bool:
    return needle.lower() in haystack.lower()


def main(yaml_path: str | None = None) -> int:
    yaml_path = yaml_path or str(Path(__file__).parent / "polish_evals.yaml")
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    cases = data.get("cases", [])

    full_cfg = _load_cfg()
    cleanup_cfg = full_cfg.get("cleanup", {})
    # Force local ollama for measurement; we are evaluating the local polish path.
    cleanup_cfg["provider"] = "ollama"
    cleaner = Cleaner(cleanup_cfg)

    model = cleanup_cfg.get("ollama", {}).get("model", "?")
    print(f"# Polish eval — provider=ollama model={model}")
    print(f"# {len(cases)} cases\n")
    print(f"{'id':<14} {'pts':<6} {'ms':<6} {'output'}")
    print("-" * 100)

    total = 0
    max_total = 0
    t_total_start = time.time()
    for case in cases:
        cid = case["id"]
        raw = case["raw"]
        tone = case.get("tone", "default")
        expected_contains = case.get("expected_contains") or []
        expected_not_contains = case.get("expected_not_contains") or []
        expected_exact = case.get("expected_exact") or []

        case_max = 2 + (1 if expected_exact else 0)
        max_total += case_max
        try:
            t0 = time.time()
            out = cleaner.clean(raw, style=tone)
            dt_ms = int((time.time() - t0) * 1000)
        except Exception as e:
            print(f"{cid:<14} ERR    -      {type(e).__name__}: {e}")
            continue

        score = 0
        # contains_all
        if expected_contains and all(_icontains(out, n) for n in expected_contains):
            score += 1
        elif not expected_contains:
            score += 1  # trivially satisfied if no requirement
        # contains_none
        if expected_not_contains and not any(_icontains(out, n) for n in expected_not_contains):
            score += 1
        elif not expected_not_contains:
            score += 1
        # exact
        if expected_exact and any(out.strip() == e for e in expected_exact):
            score += 1

        total += score
        snippet = out.replace("\n", "  ")
        if len(snippet) > 60:
            snippet = snippet[:57] + "..."
        print(f"{cid:<14} {score}/{case_max}    {dt_ms:<6} {snippet!r}")

    elapsed = time.time() - t_total_start
    pct = (100.0 * total / max_total) if max_total else 0.0
    print("-" * 100)
    print(f"TOTAL: {total} / {max_total}  ({pct:.1f}%)  elapsed={elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
