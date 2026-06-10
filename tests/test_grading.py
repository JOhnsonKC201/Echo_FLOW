"""Tests for offline self-grading (src/grade.py)."""
from __future__ import annotations

import sqlite3
import tempfile

import pytest

from src import grade as g
from src.learn import PatternMiner


# --- Whisper confidence ---


def test_whisper_confidence_high():
    meta = {"avg_logprob": -0.2, "no_speech_prob": 0.05, "compression_ratio": 1.6}
    assert g.whisper_confidence(meta) > 75


def test_whisper_confidence_silence():
    meta = {"avg_logprob": -0.9, "no_speech_prob": 0.9, "compression_ratio": 1.5}
    assert g.whisper_confidence(meta) < 30


def test_whisper_confidence_compression_penalty():
    # Repetitive hallucination triggers compression_ratio penalty.
    meta = {"avg_logprob": -0.1, "no_speech_prob": 0.05, "compression_ratio": 3.0}
    healthy = g.whisper_confidence({"avg_logprob": -0.1, "no_speech_prob": 0.05, "compression_ratio": 1.6})
    penalized = g.whisper_confidence(meta)
    assert penalized < healthy


def test_whisper_confidence_no_signal_neutral():
    assert g.whisper_confidence(None) == 50.0
    assert g.whisper_confidence({"avg_logprob": None, "no_speech_prob": None, "compression_ratio": None}) == 50.0


# --- Title-Case storm penalty ---


def test_storm_penalty_full_storm():
    """A storm used to grade 93-99 (no casing signal), so the verify pass
    never fired. A full storm must deduct enough to drop below the verify
    min_score (55)."""
    p = g.titlecase_storm_penalty("Write Me A Reply Right Now Please Sir.")
    assert p >= 45.0


def test_storm_penalty_clean_prose_with_proper_nouns():
    assert g.titlecase_storm_penalty("I met Sarah in London last week.") == 0.0
    assert g.titlecase_storm_penalty("Let's ship the migration tonight.") == 0.0


def test_storm_penalty_short_text_exempt():
    # Titles/names/short answers are legitimately capitalized.
    assert g.titlecase_storm_penalty("Morgan State University") == 0.0


def test_storm_drops_composite_below_verify_threshold():
    """End-to-end through grade(): healthy signals minus the storm penalty
    must land under 55 so cleanup.verify triggers the improvement pass."""
    meta = {"avg_logprob": -0.2, "no_speech_prob": 0.05, "compression_ratio": 1.6}
    storm = "And You Do All By Yourself Every Single Day My Friend."
    score = g.grade(raw=storm.lower(), cleaned=storm, whisper_meta=meta)
    assert score.overall < 55.0
    assert "title-case storm" in score.explanation


def test_no_storm_no_penalty_in_grade():
    meta = {"avg_logprob": -0.2, "no_speech_prob": 0.05, "compression_ratio": 1.6}
    clean = "Let's ship the migration tonight."
    score = g.grade(raw="lets ship the migration tonight", cleaned=clean,
                    whisper_meta=meta)
    assert "title-case storm" not in score.explanation


# --- Hallucination signal ---


def test_no_hallucination_clean():
    assert g.no_hallucination_score("hi how are you", "Hi, how are you?") == 100.0


def test_no_hallucination_caught():
    # Length blow-up triggers the existing _looks_hallucinated guard.
    raw = "hi"
    cleaned = "**Hi!** Here is a structured response:\n\n- Item 1\n- Item 2\n- Item 3"
    assert g.no_hallucination_score(raw, cleaned) == 0.0


# --- Pattern coverage ---


def test_pattern_coverage_neutral_when_empty():
    pm = PatternMiner(tempfile.mktemp(suffix=".db"))
    # No patterns yet → neutral 50.
    assert g.pattern_coverage("hello world", pm) == 50.0


def test_pattern_coverage_with_patterns():
    db = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE dictations (id INTEGER PRIMARY KEY, raw_text TEXT, cleaned_text TEXT, ts REAL DEFAULT 0)")
    conn.commit()
    pm = PatternMiner(db)
    # Teach it that "go" → "going" (3x repeated).
    for _ in range(3):
        pm.record("i am go store", "i am going store")
    # "going" should now be a known token.
    cov = g.pattern_coverage("I am going to store", pm)
    assert cov > 0.0   # at least one match


# --- Composite grade ---


def test_grade_clean_input_scores_high():
    score = g.grade(
        raw="hi how are you",
        cleaned="Hi, how are you?",
        whisper_meta={"avg_logprob": -0.1, "no_speech_prob": 0.05, "compression_ratio": 1.5},
        retriever=None, pattern_miner=None, learner=None,
    )
    assert score.overall > 60.0
    assert 0.0 <= score.overall <= 100.0


def test_grade_hallucinated_scores_low():
    score = g.grade(
        raw="hi",
        cleaned="**Response:** Here is a long structured analysis of your input:\n\n- Greeting detected\n- Tone: friendly\n- Suggested reply: Hello!",
        whisper_meta={"avg_logprob": -0.1, "no_speech_prob": 0.05, "compression_ratio": 1.5},
        retriever=None, pattern_miner=None, learner=None,
    )
    # Hallucination signal alone weighs ~20%, so it drags score down meaningfully.
    assert score.no_hallucination == 0.0
    assert score.overall < score.whisper_conf


def test_grade_returns_explanation():
    score = g.grade(
        raw="hello",
        cleaned="hello",
        whisper_meta={"avg_logprob": -0.9, "no_speech_prob": 0.9},
        retriever=None, pattern_miner=None, learner=None,
    )
    assert isinstance(score.explanation, str)
    assert "W=" in score.explanation   # low confidence should be called out


# --- Weights persistence ---


def test_weights_roundtrip(tmp_path):
    db = str(tmp_path / "test.db")
    custom = {"W": 0.5, "H": 0.2, "S": 0.2, "P": 0.1}
    g.save_weights(db, custom)
    loaded = g.load_weights(db)
    assert loaded == custom


def test_weights_default_when_missing(tmp_path):
    db = str(tmp_path / "missing.db")
    loaded = g.load_weights(db)
    assert loaded == g.DEFAULT_WEIGHTS


# --- Calibration ---


def test_calibration_returns_none_without_data(tmp_path):
    db = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE dictations (id INTEGER PRIMARY KEY, ts REAL)")
    conn.commit()
    assert g.calibrate_from_edits(db) is None


# --- Online weight updates ---


def _make_dictations_table(db):
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE dictations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL DEFAULT 0,
            quality_score REAL,
            quality_breakdown TEXT,
            original_cleaned TEXT,
            cleaned_text TEXT,
            raw_text TEXT
        )
    """)
    conn.commit()
    return conn


def test_update_weights_no_data_returns_none(tmp_path):
    db = str(tmp_path / "empty.db")
    _make_dictations_table(db)
    assert g.update_weights_from_edits(db) is None


def test_update_weights_converges_toward_predictive_signal(tmp_path):
    """Seed 20 fake edits where W perfectly predicts the target and the others are noise.
    After SGD, W's weight should be larger than the other three."""
    import json as _json, random
    db = str(tmp_path / "edit.db")
    conn = _make_dictations_table(db)
    rng = random.Random(0)
    for i in range(20):
        target = rng.uniform(40, 90)
        breakdown = _json.dumps({
            "whisper_conf": target,                       # perfectly predictive
            "no_hallucination": rng.uniform(0, 100),      # noise
            "semantic_coherence": rng.uniform(0, 100),    # noise
            "pattern_coverage": rng.uniform(0, 100),      # noise
        })
        # Build orig/corr pair whose edit-distance ratio yields ~target.
        # Easier: just leave orig != corr (so the row is "edited"), and rely on
        # difflib for whatever ratio results — the target is approximate.
        orig = "the quick brown fox jumps over the lazy dog"
        keep = max(1, int(len(orig) * target / 100))
        corr = orig[:keep] + "X" * (len(orig) - keep)
        conn.execute(
            "INSERT INTO dictations(ts, quality_score, quality_breakdown, "
            "original_cleaned, cleaned_text, raw_text) VALUES (?,?,?,?,?,?)",
            (i, target, breakdown, orig, corr, "raw"),
        )
    conn.commit()
    weights = g.update_weights_from_edits(db)
    assert weights is not None
    # W should dominate; others should be smaller.
    assert weights["W"] > weights["H"]
    assert weights["W"] > weights["S"]
    assert weights["W"] > weights["P"]
    # Sum to 1.
    assert abs(sum(weights.values()) - 1.0) < 1e-6


# --- Pattern decay ---


def test_decay_stale_removes_old_patterns(tmp_path):
    import time as _t
    from src.learn import PatternMiner, _ensure_patterns_table
    db = str(tmp_path / "patterns.db")
    conn = sqlite3.connect(db)
    _ensure_patterns_table(conn)
    now = _t.time()
    sixty_days_ago = now - 60 * 86400
    # Old pattern with low total → should be deleted after decay.
    conn.execute(
        "INSERT INTO learned_patterns(trigger, replacement, success, total, updated_at) "
        "VALUES ('go', 'going', 2, 2, ?)",
        (sixty_days_ago,),
    )
    # Fresh pattern from today → should survive intact.
    conn.execute(
        "INSERT INTO learned_patterns(trigger, replacement, success, total, updated_at) "
        "VALUES ('hi', 'Hi', 10, 10, ?)",
        (now,),
    )
    conn.commit()
    pm = PatternMiner(db)
    decayed, deleted = pm.decay_stale(half_life_days=14.0)
    assert decayed == 2
    assert deleted == 1
    survivors = conn.execute(
        "SELECT trigger FROM learned_patterns"
    ).fetchall()
    assert survivors == [("hi",)]
