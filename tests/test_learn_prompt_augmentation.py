"""Learner.build_prompt_augmentation — the personalized prompt suffix.

Covers src/learn.py ~lines 166-207:
- USER VOCABULARY section appears when personal vocab exists.
- RAG path: retriever hits → "SEMANTICALLY SIMILAR" examples; retriever
  misses (or absent / no query) → fallback to recent_examples.
- Disabled config or empty history → empty string (no stray separator).

No network: everything runs against a temp SQLite History db.
"""
from __future__ import annotations

import pytest

import src.learn as learn
from src.learn import Learner, LearningConfig
from src.history import History


@pytest.fixture(autouse=True)
def _reset_vocab_cache():
    """The 60s module-global vocab cache would leak between tests/dbs."""
    learn._vocab_cache = None
    learn._vocab_cache_ts = 0.0
    yield
    learn._vocab_cache = None
    learn._vocab_cache_ts = 0.0


def _make_db(tmp_path, rows):
    """rows = [(raw, cleaned)] inserted as desktop dictations, style=default."""
    db_path = str(tmp_path / "h.db")
    h = History(db_path)
    for i, (raw, cleaned) in enumerate(rows):
        h.conn.execute(
            "INSERT INTO dictations(ts, raw_text, cleaned_text, style, source) "
            "VALUES (?, ?, ?, 'default', 'desktop')",
            (float(i + 1), raw, cleaned),
        )
    h.conn.commit()
    h.conn.close()
    return db_path


class _Retriever:
    """Records search() calls; returns a preset (raw, cleaned, sim) list."""

    def __init__(self, results):
        self.results = results
        self.calls: list[tuple[str, str]] = []

    def search(self, query_text, style="default"):
        self.calls.append((query_text, style))
        return self.results


# --- (a) user vocabulary section ------------------------------------------


def test_vocab_section_included_when_vocab_exists(tmp_path):
    # "Kubernetes" appears in >= 2 cleaned dictations → personal vocab term.
    rows = [
        ("um deployed kubernetes today", "Deployed Kubernetes cluster today."),
        ("kubernetes again you know", "Kubernetes upgrade went fine."),
    ]
    lrn = Learner(_make_db(tmp_path, rows), LearningConfig())

    aug = lrn.build_prompt_augmentation("default")

    assert "USER VOCABULARY" in aug
    assert "Kubernetes" in aug
    assert aug.startswith("\n\n---\n\n")  # separator only when content exists


# --- (b) RAG examples vs recent-examples fallback --------------------------


def test_retriever_hits_used_as_semantic_examples(tmp_path):
    db = _make_db(tmp_path, [])  # empty history — examples must come from RAG
    retriever = _Retriever([("um i go store", "I went to the store.", 0.93)])
    lrn = Learner(db, LearningConfig(), retriever=retriever)

    aug = lrn.build_prompt_augmentation("default", query_text="i go store now")

    assert retriever.calls == [("i go store now", "default")]
    assert "SEMANTICALLY SIMILAR PAST DICTATIONS" in aug
    assert "RAW: um i go store" in aug
    assert "CLEANED: I went to the store." in aug
    assert "RECENT EXAMPLES" not in aug


def test_retriever_miss_falls_back_to_recent_examples(tmp_path):
    rows = [("um i go store yesterday", "I went to the store yesterday.")]
    db = _make_db(tmp_path, rows)
    retriever = _Retriever([])  # semantic search finds nothing
    lrn = Learner(db, LearningConfig(), retriever=retriever)

    aug = lrn.build_prompt_augmentation("default", query_text="store run")

    assert retriever.calls, "retriever should have been consulted first"
    assert "RECENT EXAMPLES OF HOW THIS USER'S SPEECH SHOULD BE CLEANED" in aug
    assert "RAW: um i go store yesterday" in aug
    assert "SEMANTICALLY SIMILAR" not in aug


def test_no_query_text_skips_retriever_and_uses_recent(tmp_path):
    rows = [("um i go store yesterday", "I went to the store yesterday.")]
    db = _make_db(tmp_path, rows)
    retriever = _Retriever([("should", "not appear", 0.99)])
    lrn = Learner(db, LearningConfig(), retriever=retriever)

    aug = lrn.build_prompt_augmentation("default")  # no query_text

    assert retriever.calls == []  # RAG requires query_text
    assert "RECENT EXAMPLES" in aug
    assert "RAW: um i go store yesterday" in aug


def test_recent_examples_respect_style_filter(tmp_path):
    # Only 'default'-style pairs exist; asking for another style yields none.
    rows = [("um i go store yesterday", "I went to the store yesterday.")]
    lrn = Learner(_make_db(tmp_path, rows), LearningConfig())

    aug = lrn.build_prompt_augmentation("polished")

    assert "RECENT EXAMPLES" not in aug  # no polished-style examples seeded


# --- (c) disabled / no data → empty output ---------------------------------


def test_disabled_returns_empty_string(tmp_path):
    rows = [("um i go store yesterday", "I went to the store yesterday.")]
    lrn = Learner(_make_db(tmp_path, rows), LearningConfig(enabled=False))

    assert lrn.build_prompt_augmentation("default") == ""


def test_empty_history_returns_empty_string(tmp_path):
    lrn = Learner(_make_db(tmp_path, []), LearningConfig())

    assert lrn.build_prompt_augmentation("default") == ""


def test_unreadable_db_returns_empty_string(tmp_path):
    # Nonexistent path: queries fail, both sections degrade to nothing.
    lrn = Learner(str(tmp_path / "missing" / "nope.db"), LearningConfig())

    assert lrn.build_prompt_augmentation("default") == ""
