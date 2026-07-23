"""Phonetic gate — tells an ASR mishearing from an unrelated rewrite."""
from __future__ import annotations

from src.phonetic import metaphone, phonetic_key, phonetic_similar


def test_metaphone_collapses_spelling_to_sound():
    # NODE and NOTE sound alike -> same key (the whole point).
    assert metaphone("node") == metaphone("note")
    # PHONE keys with an F sound, not a P-H.
    assert metaphone("phone").startswith("F")
    # Deterministic and non-empty for ordinary words.
    assert metaphone("vector")
    assert metaphone("vector") == metaphone("Vector")


def test_metaphone_empty_for_non_alpha():
    assert metaphone("") == ""
    assert metaphone("1234") == ""
    assert metaphone("...") == ""


def test_phonetic_key_ignores_digits_and_spacing():
    # "node2vec" and "node vec" should key the same (digits/space dropped).
    assert phonetic_key("node2vec") == phonetic_key("node vec")


def test_similar_accepts_genuine_mishearings():
    # These SOUND alike — exactly the accent errors we want to learn.
    assert phonetic_similar("note to vec", "node2vec")
    assert phonetic_similar("data set", "dataset")
    assert phonetic_similar("fast API", "FastAPI")


def test_similar_rejects_unrelated_rewrites():
    # An LLM paraphrase that changed meaning must NOT read as a mishearing.
    assert not phonetic_similar("the weather is nice", "let us ship it")
    assert not phonetic_similar("caught 14 regressions", "shows the process works")


def test_similar_rejects_empty_side():
    # No letters on a side -> unjudgeable -> never learn from it.
    assert not phonetic_similar("2024", "1999")
    assert not phonetic_similar("", "node2vec")
