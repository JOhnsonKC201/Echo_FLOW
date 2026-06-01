"""Regression: the 60s personal_vocabulary cache must not truncate a later
larger-limit request just because a smaller limit populated it first. The cache
now stores the full ranked list and slices [:limit] per call."""
from __future__ import annotations

import src.learn as learn
from src.learn import Learner, LearningConfig
from src.history import History


def _word(i: int) -> str:
    """A distinct, all-letter Capitalized word (digits would break the vocab
    regex's word boundary, so encode i in letters)."""
    s = ""
    n = i
    while True:
        s += chr(ord("a") + n % 26)
        n //= 26
        if n == 0:
            break
    return "Zeta" + s + "aa"


def _seed(tmp_path, n_terms=40):
    h = History(str(tmp_path / "h.db"))
    # Each distinct Capitalized term appears twice (>= 2 → qualifies as personal).
    for i in range(n_terms):
        term = _word(i)
        for _ in range(2):
            h.conn.execute(
                "INSERT INTO dictations(ts, raw_text, cleaned_text, source) "
                "VALUES (?, ?, ?, 'desktop')",
                (1.0, "raw", f"Discussed {term} matters.",),
            )
    h.conn.commit()
    h.conn.close()
    return str(tmp_path / "h.db")


def test_small_limit_first_does_not_truncate_later_large_limit(tmp_path):
    # Reset the module-global cache so the test is deterministic.
    learn._vocab_cache = None
    learn._vocab_cache_ts = 0.0
    db = _seed(tmp_path, n_terms=40)
    lrn = Learner(db, LearningConfig(trust_mobile=True))

    small = lrn.personal_vocabulary(2)   # populates cache first
    assert len(small) == 2

    big = lrn.personal_vocabulary(40)    # must NOT be capped to the small run
    assert len(big) == 40
