"""Casing control: learn-from-edit canon + aggressive de-Title-Casing.

Covers the two reported problems, unified:
  1. Spurious Title-Casing ("Machine Learning Feeds Here The Most") flattened.
  2. A one-time "tiktok" -> "TikTok" edit learned and re-applied forever, and
     protected from the flattener.
"""
from __future__ import annotations

import sqlite3


# ----- _polish_text flattener (pure) ---------------------------------------

def test_polish_flattens_titlecase_storm_when_protected_given():
    from src.cleanup import _polish_text
    s = ("Machine Learning Feeds Here The Most Because There Are "
         "Millions Of User And Millions Of Video.")
    out = _polish_text(s, protected=frozenset())
    # Sentence-initial cap kept; everything else lowercased.
    assert out.startswith("Machine ")
    assert "Machine learning feeds here the most" in out
    assert "Learning" not in out and "Most" not in out


def test_polish_protects_known_proper_nouns():
    from src.cleanup import _polish_text
    s = "I really love TikTok and SQL on Monday with Paris."
    protected = frozenset({"i", "paris", "monday"})
    out = _polish_text(s, protected=protected)
    assert "TikTok" in out        # internal caps never flattened
    assert "SQL" in out           # all-caps never flattened
    assert "Monday" in out        # protected (allowlist-style)
    assert "Paris" in out         # protected (dictionary-style)
    assert " I " in f" {out} "    # standalone I preserved


def test_polish_none_protected_is_legacy_no_flatten():
    from src.cleanup import _polish_text
    # Default (protected=None) must NOT flatten — preserves existing behavior.
    s = "Machine Learning Is Great."
    assert _polish_text(s) == "Machine Learning Is Great."


# ----- casing-diff + canon store -------------------------------------------

def test_diff_casing_pairs_extracts_casing_only_change():
    from src.learn import _diff_casing_pairs
    pairs = _diff_casing_pairs("i love tiktok", "i love TikTok")
    assert ("tiktok", "TikTok") in pairs
    # A different-word change is NOT a casing pair.
    assert _diff_casing_pairs("i love tiktok", "i love YouTube") == []


def test_pattern_miner_casing_roundtrip(tmp_path):
    from src.learn import PatternMiner, _invalidate_casing_cache
    db = str(tmp_path / "h.db")
    # Touch the file so sqlite can create the table.
    sqlite3.connect(db).close()
    pm = PatternMiner(db)
    n = pm.record_casing("i love tiktok", "i love TikTok")
    assert n == 1
    _invalidate_casing_cache()  # bust the 60s process cache for the assert
    canon = pm.canonical_casings()
    assert canon.get("tiktok") == "TikTok"


def test_pattern_miner_list_and_delete_casing(tmp_path):
    from src.learn import PatternMiner, _invalidate_casing_cache
    db = str(tmp_path / "h.db")
    sqlite3.connect(db).close()
    pm = PatternMiner(db)
    pm.record_casing("i love tiktok", "i love TikTok")
    pm.record_casing("on github today", "on GitHub today")
    listed = pm.list_casings()
    canonicals = {c["canonical"] for c in listed}
    assert {"TikTok", "GitHub"} <= canonicals
    assert all("count" in c and "word_lc" in c for c in listed)
    # Delete one and confirm it's gone from both the list and the canon map.
    assert pm.delete_casing("tiktok") is True
    assert pm.delete_casing("tiktok") is False  # already gone
    _invalidate_casing_cache()
    assert "TikTok" not in {c["canonical"] for c in pm.list_casings()}
    assert "tiktok" not in pm.canonical_casings()


def _seed_dictations(db: str, rows: list[tuple[str, str]]) -> None:
    """Create a minimal dictations table with (original_cleaned, cleaned_text)."""
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE dictations (id INTEGER PRIMARY KEY, ts REAL, "
        "original_cleaned TEXT, cleaned_text TEXT)"
    )
    for i, (oc, ct) in enumerate(rows):
        conn.execute(
            "INSERT INTO dictations (ts, original_cleaned, cleaned_text) VALUES (?,?,?)",
            (float(i), oc, ct),
        )
    conn.commit()
    conn.close()


def test_backfill_casings_mines_user_edits_once(tmp_path):
    from src.learn import PatternMiner, _invalidate_casing_cache
    db = str(tmp_path / "h.db")
    _seed_dictations(db, [
        ("i opened tiktok today", "i opened TikTok today"),  # casing-only edit
        ("we shipped the migration", "we shipped the release"),  # word change, ignored
    ])
    pm = PatternMiner(db)
    seeded = pm.backfill_casings_from_history()
    assert seeded == 1
    _invalidate_casing_cache()
    assert pm.canonical_casings().get("tiktok") == "TikTok"
    # Second call is a no-op (one-shot guard) — respects later deletions.
    assert pm.backfill_casings_from_history() == 0


# ----- Cleaner._apply_learned_casing + _finalize ---------------------------

class _FakeMiner:
    def __init__(self, canon):
        self._canon = canon

    def canonical_casings(self):
        return dict(self._canon)

    def confident_patterns(self, min_confidence=0.7):
        return {}


def _cleaner_with_canon(canon):
    from src.cleanup import Cleaner
    c = Cleaner({"enabled": True, "provider": "ollama"})
    c._pattern_miner = _FakeMiner(canon)
    return c


def test_apply_learned_casing_forces_canonical_form():
    c = _cleaner_with_canon({"tiktok": "TikTok"})
    assert c._apply_learned_casing("i opened tiktok") == "i opened TikTok"
    assert c._apply_learned_casing("I OPENED TIKTOK") == "I OPENED TikTok"
    assert c._apply_learned_casing("Tiktok rocks") == "TikTok rocks"


def test_finalize_applies_canon_and_protects_it_from_flatten():
    c = _cleaner_with_canon({"tiktok": "TikTok"})
    # Title-Case storm in, with a lowercase tiktok that must become TikTok and
    # survive the flattener.
    out = c._finalize("I Downloaded Tiktok And Watched Videos.")
    assert "TikTok" in out
    assert "downloaded" in out and "watched" in out  # storm flattened
    assert "Downloaded" not in out


def test_finalize_skips_prompt_style():
    c = _cleaner_with_canon({"tiktok": "TikTok"})
    s = "Rewrite This As A Clear Instruction."
    assert c._finalize(s, style="prompt") == s


def test_finalize_respects_flatten_disabled():
    from src.cleanup import Cleaner
    c = Cleaner({"enabled": True, "provider": "ollama",
                 "casing": {"flatten_titlecase": False}})
    c._pattern_miner = _FakeMiner({})
    out = c._finalize("Machine Learning Is Great.")
    # No flatten — Title Case preserved (legacy polish still adds nothing here).
    assert "Machine Learning Is Great" in out


# ----- end-to-end via clean() ----------------------------------------------

def test_clean_skip_path_flattens_titlecase():
    c = _cleaner_with_canon({})
    out, skipped = c.clean("Machine Learning Feeds Here The Most.")
    assert skipped is True  # short + capitalized + punctuated => skip LLM
    assert out.startswith("Machine ")
    assert "learning feeds here the most" in out


def test_clean_llm_path_finalizes_model_titlecase(monkeypatch):
    c = _cleaner_with_canon({})
    # Force the LLM path (messy input) and have the model emit Title Case.
    monkeypatch.setattr(
        c, "_via_ollama",
        lambda system, text, **kw: "Echo Should Not Capitalize Every Word.",
    )
    out, skipped = c.clean("um echo should not capitalize every word")
    assert skipped is False
    assert "should not capitalize every word" in out
    assert "Should" not in out
