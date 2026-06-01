"""Offline self-grading: scores each dictation 0-100 from local signals only.

Four signals, weighted:
  W = Whisper confidence (avg_logprob + no_speech_prob)            ~40%
  H = No-hallucination (cleanup didn't go off-track)                ~20%
  S = Semantic coherence (raw vs cleaned embedding cosine)          ~20%
  P = Pattern coverage  (% words covered by learned vocab/patterns) ~20%

No network calls. No LLM. Pure local heuristics + the already-loaded
sentence-transformers model from src/retrieval.py.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, asdict


# Default weights — calibrate_from_edits() can override these.
DEFAULT_WEIGHTS = {"W": 0.40, "H": 0.20, "S": 0.20, "P": 0.20}


@dataclass
class QualityScore:
    overall: float          # 0–100 composite
    whisper_conf: float     # 0–100
    no_hallucination: float
    semantic_coherence: float
    pattern_coverage: float
    explanation: str

    def to_json(self) -> str:
        return json.dumps(asdict(self))


# --- Individual signal functions ---


def whisper_confidence(meta: dict | None) -> float:
    """Convert Whisper's avg_logprob + no_speech_prob into 0-100.

    Whisper's avg_logprob is typically in [-1.0, 0.0] for good speech.
    no_speech_prob in [0, 1]; >0.5 means likely silence.
    """
    if not meta:
        return 50.0   # no signal → neutral
    lp = meta.get("avg_logprob")
    nsp = meta.get("no_speech_prob")
    if lp is None and nsp is None:
        return 50.0
    # avg_logprob: -1.0 → 0, 0.0 → 100. Clip outside that range.
    lp_score = 100.0 if lp is None else max(0.0, min(100.0, (lp + 1.0) * 100.0))
    # no_speech_prob: 0.0 → 100, 1.0 → 0
    ns_score = 100.0 if nsp is None else max(0.0, min(100.0, (1.0 - nsp) * 100.0))
    # Penalize hallucination via compression_ratio: typical speech ~1.5-2.2.
    # Whisper's own rule of thumb: >2.4 is repetitive hallucination.
    cr = meta.get("compression_ratio")
    cr_penalty = 0.0
    if cr is not None and cr > 2.4:
        cr_penalty = min(40.0, (cr - 2.4) * 50.0)
    base = 0.6 * lp_score + 0.4 * ns_score - cr_penalty
    return max(0.0, min(100.0, base))


def no_hallucination_score(raw: str, cleaned: str) -> float:
    """Reuse cleanup.py's hallucination guard. Pass=100, fail=0."""
    try:
        from .cleanup import Cleaner
        return 0.0 if Cleaner._looks_hallucinated(raw, cleaned) else 100.0
    except Exception:
        return 100.0


def semantic_coherence(raw: str, cleaned: str, retriever) -> float:
    """Cosine similarity between raw and cleaned embeddings → 0-100.

    Cleanup that preserves meaning sits in [0.85, 1.0]. Below 0.7 means
    the model rewrote the content entirely (suspicious).
    """
    if not raw.strip() or not cleaned.strip() or retriever is None:
        return 50.0
    try:
        v_raw = retriever.embed_text(raw)
        v_cln = retriever.embed_text(cleaned)
    except Exception:
        return 50.0
    if v_raw is None or v_cln is None:
        return 50.0
    import numpy as np
    sim = float(np.dot(v_raw, v_cln))   # both L2-normalized → cosine
    # Map [0.5, 1.0] → [0, 100]; clip below 0.5.
    return max(0.0, min(100.0, (sim - 0.5) * 200.0))


def pattern_coverage(cleaned: str, pattern_miner, learner=None) -> float:
    """Fraction of word tokens that match a confident learned pattern or vocab."""
    if not cleaned.strip():
        return 50.0
    tokens = re.findall(r"[A-Za-z']+", cleaned)
    if not tokens:
        return 50.0
    covered = 0
    patterns = {}
    vocab: set[str] = set()
    try:
        if pattern_miner is not None:
            patterns = pattern_miner.confident_patterns()
        if learner is not None:
            vocab = {v.lower() for v in learner.personal_vocabulary(200)}
    except Exception:
        pass
    if not patterns and not vocab:
        # First-use case: give a neutral score so we don't penalize new users.
        return 50.0
    # A cleaned token is "known" if it's either a trigger (mishear we've seen
    # before) or a replacement (the corrected form we've learned), or in the
    # user's personal vocabulary.
    known: set[str] = set(patterns.keys()) | {v.lower() for v in patterns.values()} | vocab
    for tok in tokens:
        if tok.lower() in known:
            covered += 1
    return 100.0 * covered / len(tokens)


# --- Composite ---


def _explain(s: "QualityScore") -> str:
    parts = []
    if s.whisper_conf < 50: parts.append(f"W={s.whisper_conf:.0f} (low audio conf)")
    if s.no_hallucination < 50: parts.append("H=0 (cleanup hallucinated)")
    if s.semantic_coherence < 50: parts.append(f"S={s.semantic_coherence:.0f} (meaning drift)")
    if s.pattern_coverage < 30: parts.append(f"P={s.pattern_coverage:.0f} (few known terms)")
    if not parts:
        return "all signals healthy"
    return ", ".join(parts)


def grade(
    raw: str,
    cleaned: str,
    whisper_meta: dict | None,
    retriever=None,
    pattern_miner=None,
    learner=None,
    weights: dict | None = None,
) -> QualityScore:
    """Compute a 0-100 quality score from offline signals."""
    w = weights or DEFAULT_WEIGHTS
    W = whisper_confidence(whisper_meta)
    H = no_hallucination_score(raw, cleaned)
    S = semantic_coherence(raw, cleaned, retriever)
    P = pattern_coverage(cleaned, pattern_miner, learner)
    overall = w["W"] * W + w["H"] * H + w["S"] * S + w["P"] * P
    score = QualityScore(
        overall=round(overall, 1),
        whisper_conf=round(W, 1),
        no_hallucination=round(H, 1),
        semantic_coherence=round(S, 1),
        pattern_coverage=round(P, 1),
        explanation="",
    )
    score.explanation = _explain(score)
    return score


# --- Calibration against user corrections ---


def _ensure_weights_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS grading_weights (
            signal TEXT PRIMARY KEY,
            weight REAL NOT NULL
        )
        """
    )


def load_weights(db_path: str) -> dict:
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            _ensure_weights_table(conn)
            rows = conn.execute("SELECT signal, weight FROM grading_weights").fetchall()
    except Exception:
        return dict(DEFAULT_WEIGHTS)
    if not rows:
        return dict(DEFAULT_WEIGHTS)
    out = {k: float(v) for k, v in rows}
    for k in DEFAULT_WEIGHTS:
        out.setdefault(k, DEFAULT_WEIGHTS[k])
    return out


def save_weights(db_path: str, weights: dict) -> None:
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            _ensure_weights_table(conn)
            for k, v in weights.items():
                conn.execute(
                    "INSERT OR REPLACE INTO grading_weights(signal, weight) VALUES (?, ?)",
                    (k, float(v)),
                )
            conn.commit()
    except Exception:
        pass


def update_weights_from_edits(db_path: str, sample: int = 50) -> dict | None:
    """Adjust the four W/H/S/P weights via SGD to better predict edit-free dictations.

    For each of the last N user-edited dictations:
      - target = (1 - edit_distance_ratio) * 100  → the "true" quality the system
        should have predicted (a perfectly-fixed dictation had 0 edits → 100,
        a heavily-rewritten one had high edit distance → near 0).
      - features = (W, H, S, P) from the stored quality_breakdown JSON.

    Fits weights by gradient descent on MSE between (w · features) and target.
    Constrains weights ≥ 0 and renormalizes to sum to 1.

    Returns the new weights (also persists via save_weights), or None if not
    enough edited dictations exist to learn from (< 10).
    """
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(dictations)").fetchall()]
            if "original_cleaned" not in cols or "quality_breakdown" not in cols:
                return None
            rows = conn.execute(
                "SELECT quality_breakdown, original_cleaned, cleaned_text FROM dictations "
                "WHERE quality_breakdown IS NOT NULL AND original_cleaned IS NOT NULL "
                "AND original_cleaned != cleaned_text "
                "ORDER BY ts DESC LIMIT ?",
                (sample,),
            ).fetchall()
    except Exception:
        return None
    if len(rows) < 10:
        return None

    import difflib
    samples: list[tuple[list[float], float]] = []
    for breakdown, orig, corr in rows:
        try:
            b = json.loads(breakdown)
        except Exception:
            continue
        feats = [
            float(b.get("whisper_conf", 50.0)),
            float(b.get("no_hallucination", 50.0)),
            float(b.get("semantic_coherence", 50.0)),
            float(b.get("pattern_coverage", 50.0)),
        ]
        sm = difflib.SequenceMatcher(a=orig or "", b=corr or "", autojunk=False)
        edit_ratio = 1.0 - sm.ratio()
        # Clip to [0, 95] so even perfect fixes don't train weights to predict 100.
        target = max(0.0, min(95.0, (1.0 - edit_ratio) * 100.0))
        samples.append((feats, target))
    if len(samples) < 10:
        return None

    # SGD on MSE, starting from current weights (or defaults).
    current = load_weights(db_path)
    w = [current.get(k, DEFAULT_WEIGHTS[k]) for k in ("W", "H", "S", "P")]
    lr = 0.0005
    for _ in range(40):
        for feats, target in samples:
            pred = sum(w[i] * feats[i] for i in range(4))
            err = pred - target
            for i in range(4):
                w[i] -= lr * err * feats[i] / 100.0
            # Clamp to [0, +∞)
            w = [max(0.0, x) for x in w]
    # Renormalize so sum = 1.
    s = sum(w) or 1.0
    new_weights = {"W": w[0]/s, "H": w[1]/s, "S": w[2]/s, "P": w[3]/s}
    save_weights(db_path, new_weights)
    return new_weights


def calibrate_from_edits(db_path: str, sample: int = 100) -> float | None:
    """Compute Pearson r between quality scores and edit distance to user corrections.

    Returns r, or None if not enough data. A more negative r is better
    (higher quality → smaller correction edit).
    """
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(dictations)").fetchall()]
            if "original_cleaned" not in cols or "quality_score" not in cols:
                return None
            rows = conn.execute(
                "SELECT quality_score, original_cleaned, cleaned_text FROM dictations "
                "WHERE quality_score IS NOT NULL AND original_cleaned IS NOT NULL "
                "AND original_cleaned != cleaned_text "
                "ORDER BY ts DESC LIMIT ?",
                (sample,),
            ).fetchall()
    except Exception:
        return None
    if len(rows) < 5:
        return None
    import difflib
    xs: list[float] = []
    ys: list[float] = []
    for q, orig, corr in rows:
        sm = difflib.SequenceMatcher(a=orig or "", b=corr or "", autojunk=False)
        edit = 1.0 - sm.ratio()
        xs.append(float(q))
        ys.append(edit)
    n = len(xs)
    mx = sum(xs) / n; my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)
