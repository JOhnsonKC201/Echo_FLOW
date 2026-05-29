"""End-to-end cleanup latency harness — text-only (skips ASR).

Sits next to run_polish_evals.py but answers a different question: not
"how good is cleanup?" but "how fast and consistent is it, by provider?".
Reuses the same yaml corpus so the inputs are comparable.

For each (provider, case) pair, runs cleaner.clean() with a cold-start +
N warm iterations, then reports:
  - per-provider p50 / p95 / p99 wall-clock (ms)
  - per-provider mean output length / input length ratio
  - per-provider error count (LLM unreachable, timeout, etc.)
  - per-case slowest example so you can spot pathological prompts

Usage (from repo root):
    .venv\\Scripts\\python.exe tests\\eval\\measure_e2e.py
    .venv\\Scripts\\python.exe tests\\eval\\measure_e2e.py --providers ollama,learned
    .venv\\Scripts\\python.exe tests\\eval\\measure_e2e.py --iters 3 --warmup 1

Does NOT exercise audio capture or Whisper — that requires hardware. For
ASR timing, dictate ten utterances with the daemon and read
data/wispr.log for the timed lines, or extend audio.py with a fixture.
"""
from __future__ import annotations

import argparse
import statistics
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


def _load_cases(yaml_path: Path) -> list[dict]:
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("cases", [])


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(pct / 100.0 * (len(s) - 1)))))
    return s[k]


def _run_provider(provider: str, cases: list[dict], cleanup_cfg: dict,
                  iters: int, warmup: int) -> dict:
    cfg_copy = dict(cleanup_cfg)
    cfg_copy["provider"] = provider
    # Force the LLM polish path on every iteration so we're measuring the
    # provider, not the skip_when_clean fast-path.
    cfg_copy["skip_when_clean"] = False
    cleaner = Cleaner(cfg_copy)

    samples: list[float] = []
    errors = 0
    in_chars = 0
    out_chars = 0
    slowest: tuple[float, str, str] | None = None  # (ms, case_id, preview)

    for case in cases:
        cid = case["id"]
        raw = case["raw"]
        tone = case.get("tone", "default")

        # Warmup (not measured) — kicks Ollama into loading the model.
        for _ in range(max(0, warmup)):
            try:
                cleaner.clean(raw, style=tone)
            except Exception:
                pass

        # Measured iterations.
        for _ in range(iters):
            t0 = time.time()
            try:
                out = cleaner.clean(raw, style=tone)
            except Exception:
                errors += 1
                continue
            dt_ms = (time.time() - t0) * 1000.0
            samples.append(dt_ms)
            in_chars += len(raw)
            out_chars += len(out or "")
            if slowest is None or dt_ms > slowest[0]:
                preview = (out or "")[:50].replace("\n", " ")
                slowest = (dt_ms, cid, preview)

    return {
        "provider": provider,
        "n": len(samples),
        "errors": errors,
        "p50": _percentile(samples, 50),
        "p95": _percentile(samples, 95),
        "p99": _percentile(samples, 99),
        "mean": statistics.fmean(samples) if samples else 0.0,
        "stdev": statistics.pstdev(samples) if len(samples) > 1 else 0.0,
        "expansion_ratio": (out_chars / in_chars) if in_chars else 0.0,
        "slowest": slowest,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--providers", default="ollama,learned",
        help="Comma-separated providers to benchmark. Default: ollama,learned",
    )
    ap.add_argument("--iters", type=int, default=2, help="Measured iterations per case")
    ap.add_argument("--warmup", type=int, default=1, help="Warmup iterations per case")
    ap.add_argument(
        "--corpus", default=str(Path(__file__).parent / "polish_evals.yaml"),
        help="YAML corpus path (same schema as polish_evals.yaml)",
    )
    args = ap.parse_args()

    cases = _load_cases(Path(args.corpus))
    full_cfg = _load_cfg()
    cleanup_cfg = full_cfg.get("cleanup", {})

    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    print(f"# E2E cleanup latency — {len(cases)} cases, {args.iters} iter + {args.warmup} warmup")
    print(f"# corpus = {args.corpus}")
    print(f"# providers = {providers}\n")

    results = []
    for prov in providers:
        print(f"running {prov}...")
        r = _run_provider(prov, cases, cleanup_cfg, args.iters, args.warmup)
        results.append(r)

    print()
    header = f"{'provider':<12} {'n':>4} {'err':>4} {'p50':>7} {'p95':>7} {'p99':>7} {'mean':>7} {'std':>6} {'out/in':>7}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r['provider']:<12} {r['n']:>4} {r['errors']:>4} "
            f"{r['p50']:>6.0f}ms {r['p95']:>6.0f}ms {r['p99']:>6.0f}ms "
            f"{r['mean']:>6.0f}ms {r['stdev']:>5.0f}ms {r['expansion_ratio']:>7.2f}"
        )

    print("\nslowest single call per provider:")
    for r in results:
        if r["slowest"]:
            ms, cid, preview = r["slowest"]
            print(f"  {r['provider']:<12} {ms:6.0f}ms  {cid:<14} {preview!r}")
        else:
            print(f"  {r['provider']:<12} (no samples)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
