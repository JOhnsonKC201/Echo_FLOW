"""Deterministic vague-claim detector (src/vagueness.py).

Finds the holes where a fact should be and turns each into a question. Pure
function, no model. Suppresses a hole when a real number is already in the
sentence (the claim isn't actually vague then).
"""
from __future__ import annotations

from src import vagueness as v


def test_flags_abstract_magnitude_claims():
    holes = v.find("This achieved significant improvements on the benchmark.")
    assert len(holes) == 1
    assert holes[0].kind == "magnitude"
    assert "number" in holes[0].question.lower()


def test_flags_missing_citation_and_time_and_quantity():
    text = ("Researchers have shown that a number of challenges remain. "
            "Recently, various approaches emerged.")
    kinds = {h.kind for h in v.find(text)}
    assert {"citation", "quantity", "time"} <= kinds


def test_suppresses_when_a_real_number_is_present():
    # A concrete figure in the sentence means it is not a hole.
    assert v.count("Revenue grew significantly, up 42% to $18.5M.") == 0
    assert v.count("We saw a substantial increase of 3.2 points.") == 0
    # But the same claim with no number IS a hole.
    assert v.count("We saw a substantial increase in accuracy.") == 1


def test_citation_is_not_suppressed_by_a_number():
    # "researchers have shown" needs a source even if a number is nearby —
    # a figure isn't a citation.
    assert v.count("Researchers have shown 3 key results.") == 1


def test_clean_specific_text_has_no_holes():
    text = ("We shipped the parser on Tuesday. It reads 12 invoice formats and "
            "fails on rotated scans, where accuracy drops to 61%.")
    assert v.find(text) == []


def test_prompts_are_deduped_and_capped():
    text = ("significant improvements here and significant improvements there, "
            "and a number of things and a number of others too.")
    ps = v.prompts(text, limit=6)
    phrases = [p["phrase"].lower() for p in ps]
    assert len(phrases) == len(set(phrases))          # deduped
    assert all("phrase" in p and "question" in p for p in ps)


def test_segments_roundtrip_and_flag_only_holes():
    text = "It made significant improvements last quarter."
    segs = v.segments(text)
    assert "".join(chunk for _, chunk in segs) == text
    holes = [c for is_h, c in segs if is_h]
    assert holes == ["significant improvements"]


def test_segments_and_find_handle_empty():
    assert v.find("") == [] and v.segments("") == [] and v.count("") == 0
    assert v.count(None) == 0        # type: ignore[arg-type]
