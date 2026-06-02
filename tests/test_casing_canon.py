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


# ----- possessives: a protected proper noun keeps its case in "Noun's" -------

def test_polish_protects_possessive_of_protected_noun():
    from src.cleanup import _polish_text
    # "London's" must survive: the possessive 's must not defeat the protected
    # lookup (which only knows the base word "london").
    out = _polish_text("We Studied London's History.", protected=frozenset({"london"}))
    assert "London's" in out
    assert "studied" in out and "history" in out  # storm still flattened


def test_polish_flattens_unprotected_possessive():
    from src.cleanup import _polish_text
    # An ordinary mid-sentence possessive is still flattened, suffix preserved.
    out = _polish_text("The Manager's Plan Failed.", protected=frozenset())
    assert "manager's" in out and "plan" in out and "failed" in out
    assert "Manager's" not in out


def test_polish_internal_caps_possessive_preserved():
    from src.cleanup import _polish_text
    # "TikTok's" already survives (internal cap), but lock it in.
    out = _polish_text("I Watched TikTok's Feed.", protected=frozenset())
    assert "TikTok's" in out


def test_polish_uppercase_S_possessive_normalized():
    from src.cleanup import _polish_text
    # Whisper's Title-Case storm capitalizes the possessive S too. A protected
    # noun keeps its case; the suffix normalizes to lowercase 's.
    out = _polish_text("I Loved London'S Cafes.", protected=frozenset({"london"}))
    assert "London's" in out and "London'S" not in out
    assert "loved" in out and "cafes" in out
    # An ordinary word flattens fully, suffix included.
    out2 = _polish_text("The Manager'S Plan Failed.", protected=frozenset())
    assert "manager's" in out2 and "Manager'S" not in out2


def test_polish_bare_apostrophe_possessive():
    from src.cleanup import _polish_text
    # Plural/trailing-apostrophe possessive: protected noun preserved, suffix kept.
    out = _polish_text("We Crossed Texas' Border.", protected=frozenset({"texas"}))
    assert "Texas'" in out and "border" in out
    out2 = _polish_text("The Managers' Plan Failed.", protected=frozenset())
    assert "managers'" in out2 and "Managers'" not in out2


def test_apply_learned_casing_bare_apostrophe():
    c = _cleaner_with_canon({"tiktok": "TikTok"})
    assert c._apply_learned_casing("the tiktok' brand") == "the TikTok' brand"


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


def test_add_casing_directly(tmp_path):
    from src.learn import PatternMiner, _invalidate_casing_cache
    db = str(tmp_path / "h.db")
    sqlite3.connect(db).close()
    pm = PatternMiner(db)
    # Teach a casing straight from the dashboard, no Fix-dialog round trip.
    assert pm.add_casing("TikTok") == "TikTok"
    _invalidate_casing_cache()
    assert pm.canonical_casings().get("tiktok") == "TikTok"
    # Re-adding the same word with new casing overrides it (acts as an edit).
    assert pm.add_casing("TIKTOK") == "TIKTOK"
    _invalidate_casing_cache()
    assert pm.canonical_casings().get("tiktok") == "TIKTOK"


def test_add_casing_rejects_garbage(tmp_path):
    from src.learn import PatternMiner
    db = str(tmp_path / "h.db")
    sqlite3.connect(db).close()
    pm = PatternMiner(db)
    assert pm.add_casing("alllower") is None       # no meaningful casing
    assert pm.add_casing("two Words") is None       # not a single token
    assert pm.add_casing("") is None
    assert pm.add_casing("   ") is None
    assert pm.add_casing("a1") is None              # no meaningful capital
    assert pm.add_casing("A" + "a" * 100) is None   # over the 80-char cap


def test_add_casing_strips_possessive(tmp_path):
    from src.learn import PatternMiner, _invalidate_casing_cache
    db = str(tmp_path / "h.db")
    sqlite3.connect(db).close()
    pm = PatternMiner(db)
    # "London's" teaches the base word so the canon row isn't dead weight
    # (the apply path strips the possessive before lookup).
    assert pm.add_casing("London's") == "London"
    assert pm.add_casing("James'") == "James"
    _invalidate_casing_cache()
    canon = pm.canonical_casings()
    assert canon.get("london") == "London" and "london's" not in canon


def test_add_casing_allows_digit_tokens(tmp_path):
    from src.learn import PatternMiner, _invalidate_casing_cache
    db = str(tmp_path / "h.db")
    sqlite3.connect(db).close()
    pm = PatternMiner(db)
    assert pm.add_casing("iOS17") == "iOS17"
    _invalidate_casing_cache()
    assert pm.canonical_casings().get("ios17") == "iOS17"


def test_add_casing_count_distinguishes_reinforce_from_override(tmp_path):
    from src.learn import PatternMiner, _invalidate_casing_cache
    db = str(tmp_path / "h.db")
    sqlite3.connect(db).close()
    pm = PatternMiner(db)
    pm.add_casing("TikTok")            # count 1
    pm.add_casing("TikTok")            # same form -> reinforce -> count 2
    pm.add_casing("TIKTOK")            # corrective override -> count unchanged
    _invalidate_casing_cache()
    row = next(c for c in pm.list_casings() if c["word_lc"] == "tiktok")
    assert row["canonical"] == "TIKTOK"   # override applied
    assert row["count"] == 2              # not inflated to 3 by the edit


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


def test_backfill_realistic_history(tmp_path):
    from src.learn import PatternMiner, _invalidate_casing_cache
    db = str(tmp_path / "h.db")
    _seed_dictations(db, [
        ("i use github daily", "i use GitHub daily"),     # casing-only -> learn
        ("we met sarah", "we met Sarah"),                  # casing-only -> learn
        ("the migration ran", "the migration ran"),        # no edit -> ignore
        ("deploy to staging", "deploy to production"),     # word change -> ignore
        ("i like javascript", "i like JavaScript"),        # casing-only -> learn
    ])
    pm = PatternMiner(db)
    seeded = pm.backfill_casings_from_history()
    assert seeded == 3
    _invalidate_casing_cache()
    canon = pm.canonical_casings()
    assert canon == {"github": "GitHub", "sarah": "Sarah", "javascript": "JavaScript"}


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


def test_apply_learned_casing_handles_possessive():
    c = _cleaner_with_canon({"tiktok": "TikTok"})
    # A learned casing must apply through the possessive 's.
    assert c._apply_learned_casing("i saw tiktok's algorithm") == "i saw TikTok's algorithm"
    # ALLCAPS possessive normalizes the suffix to lowercase 's.
    assert c._apply_learned_casing("TIKTOK'S reach") == "TikTok's reach"
    # A plain word ending in s (no apostrophe) is untouched by the strip.
    assert c._apply_learned_casing("tiktok rocks") == "TikTok rocks"


def test_finalize_protects_possessive_via_canon_and_bundle():
    c = _cleaner_with_canon({"tiktok": "TikTok"})
    out = c._finalize("I Studied Tiktok's Algorithm In London's Cafes.")
    assert "TikTok's" in out          # canon applied through the possessive
    assert "London's" in out          # bundled proper noun protected through it
    assert "studied" in out and "algorithm" in out and "cafes" in out
    assert "Studied" not in out and "Algorithm" not in out


def test_finalize_applies_canon_and_protects_it_from_flatten():
    c = _cleaner_with_canon({"tiktok": "TikTok"})
    # Title-Case storm in, with a lowercase tiktok that must become TikTok and
    # survive the flattener.
    out = c._finalize("I Downloaded Tiktok And Watched Videos.")
    assert "TikTok" in out
    assert "downloaded" in out and "watched" in out  # storm flattened
    assert "Downloaded" not in out


def test_finalize_protects_bundled_proper_nouns():
    # Untaught but common proper nouns survive the flattener by default.
    c = _cleaner_with_canon({})
    out = c._finalize("We Deployed To London And Tokyo Using Docker.")
    assert "London" in out and "Tokyo" in out and "Docker" in out
    # ...while genuinely spurious Title-Case is still flattened.
    assert "Deployed" not in out and "Using" not in out


def test_multiword_proper_noun_distinctive_word_survives():
    from src.cleanup import _polish_text
    # Per-word flatten: the distinctive word (York/Diego, protected) survives;
    # the ordinary-word head (New/San) lowercases. Better than losing both.
    protected = frozenset({"york", "diego"})
    out = _polish_text("We flew to New York and San Diego last week.",
                       protected=protected)
    assert "York" in out and "Diego" in out
    # A genuine storm still flattens fully.
    out2 = _polish_text("Machine Learning Feeds Here The Most.", protected=protected)
    assert "Machine learning feeds here the most" in out2


def test_bundled_multiword_places_via_finalize():
    c = _cleaner_with_canon({})
    out = c._finalize("We Opened Offices In New York And South Korea.")
    # Distinctive words protected by the bundled allowlist survive...
    assert "York" in out and "Korea" in out
    # ...while genuine verbs/nouns are flattened.
    assert "Opened" not in out and "Offices" not in out


def test_protect_common_nouns_can_be_disabled():
    from src.cleanup import Cleaner
    c = Cleaner({"enabled": True, "provider": "ollama",
                 "casing": {"protect_common_nouns": False}})
    c._pattern_miner = _FakeMiner({})
    out = c._finalize("We Met Sarah In London.")
    # With the bundle off, untaught names/places get flattened.
    assert "london" in out and "sarah" in out


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
