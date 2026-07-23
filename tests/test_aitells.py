"""Deterministic AI-tell detector (src/aitells.py).

Pure function, no model. Contract: flags the mechanical tells the humanize prompt
is asked to remove, returns spans that index into the original text, and never
double-counts a phrase and a word nested inside it.
"""
from __future__ import annotations

from src import aitells


CLEAN = "We shipped the feature yesterday. It works. The team is happy with it."

AI = ("It's important to note that this is a testament to our robust culture. "
      "Moreover, we leverage seamless synergies and navigate the evolving "
      "landscape. It's not just good, it's great.")


def test_clean_human_text_scores_zero():
    assert aitells.score(CLEAN) == 0
    assert aitells.find(CLEAN) == []
    assert aitells.phrases(CLEAN) == []


def test_ai_text_scores_high():
    assert aitells.score(AI) >= 8


def test_detects_vocabulary_on_word_boundaries():
    assert aitells.score("We delve into it.") == 1
    # A word merely containing a tell as a substring is not a hit.
    assert aitells.score("The robustness suite passed.") == 0
    assert aitells.score("She fostered a cat.") == 0        # 'foster' but not 'fostering'/'foster'
    assert aitells.score("Fostering trust matters.") == 1


def test_detects_the_antithesis_tic():
    assert aitells.score("It's not just fast, it's reliable.") >= 1
    assert aitells.score("This isn't only about speed.") >= 1


def test_detects_hedging_and_throat_clearing():
    assert aitells.score("It's important to note that we shipped.") >= 1
    assert aitells.score("When it comes to testing, we care.") >= 1
    assert aitells.score("In today's fast-paced world, speed wins.") >= 1


def test_em_dash_rhythm_is_flagged_but_hyphenated_words_are_not():
    assert aitells.score("The result — surprising — held up.") >= 1
    assert aitells.score("a — b") == 1
    # A hyphenated compound is normal writing, not an em-dash tell.
    assert aitells.score("a well-tested, low-latency path") == 0


def test_hits_carry_usable_spans():
    text = "We delve into it."
    (hit,) = aitells.find(text)
    assert text[hit.start:hit.end].lower() == "delve"
    assert hit.kind == "vocabulary"


def test_no_double_counting_of_nested_matches():
    # "it's important to note" (phrase) contains no vocab word, but a phrase and
    # an overlapping vocab hit must not both count. Construct an overlap:
    text = "when it comes to leverage"      # throat-clearing phrase + 'leverage'
    hits = aitells.find(text)
    # The phrase and the non-overlapping vocab word are both real and disjoint.
    kinds = sorted(h.kind for h in hits)
    assert kinds == ["throat-clearing", "vocabulary"]
    # Spans never overlap.
    for a, b in zip(hits, hits[1:]):
        assert a.end <= b.start


def test_phrases_are_deduped_and_capped():
    text = "delve delve delve moreover moreover"
    assert aitells.phrases(text) == ["delve", "moreover"]
    many = " ".join(["delve moreover furthermore crucial pivotal"] * 5)
    assert len(aitells.phrases(many, limit=3)) == 3


def test_handles_empty_and_none():
    assert aitells.score("") == 0
    assert aitells.score(None) == 0        # type: ignore[arg-type]
    assert aitells.find("") == []


def test_detects_expanded_vocabulary_and_phrases():
    assert aitells.score("We leverage synergies to spearhead the roadmap.") >= 3
    assert aitells.score("It plays a crucial role in the process.") >= 1
    assert aitells.score("A wide range of options is available.") >= 1
    assert aitells.score("In conclusion, this unlocks the potential of growth.") >= 2
    assert aitells.score("This is a testament to the team.") >= 1


def test_segments_roundtrip_exactly():
    for t in ["", "clean human text here",
              "Moreover, we leverage seamless synergy.",
              "delve — into it"]:
        segs = aitells.segments(t)
        assert "".join(chunk for _, chunk in segs) == t


def test_segments_flag_only_the_tells():
    segs = aitells.segments("We delve into it daily.")
    tells = [c for is_t, c in segs if is_t]
    plain = "".join(c for is_t, c in segs if not is_t)
    assert tells == ["delve"]
    assert "into it daily" in plain


def test_segments_emit_no_markup():
    for _, chunk in aitells.segments("<b>moreover</b> we leverage things"):
        assert "<mark" not in chunk and "<span" not in chunk


def test_segments_empty_text():
    assert aitells.segments("") == []


def test_detects_tight_em_dash_not_just_spaced():
    # The bug: "word—word" (no surrounding spaces) used to score 0.
    assert aitells.score("wrong extractions—confidently reading") == 1
    assert aitells.score("a — spaced — dash") == 2
    assert aitells.score("the 1990–2000 range") == 1        # en dash
    assert aitells.score("do it a - b style") == 1               # spaced hyphen


def test_tight_ascii_hyphen_compound_is_not_a_dash():
    # A hyphenated compound is normal writing, not a dash tell.
    s = aitells.score("a well-tested, low-latency path")
    assert s == 0


def test_detects_stiff_comma_led_transitions():
    assert aitells.score("Additionally, we shipped it.") >= 1
    assert aitells.score("However, it works.") >= 1
    # Without the trailing comma it is not flagged (ordinary use).
    assert aitells.score("however you like") == 0
