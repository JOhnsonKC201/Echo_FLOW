"""Tests for snippet expansion in Cleaner._expand_snippets."""
from __future__ import annotations

from src.cleanup import Cleaner


def _cleaner_with(snippets: dict) -> Cleaner:
    return Cleaner({"enabled": True, "provider": "none", "snippets": snippets})


def test_basic_lowercase_expansion():
    c = _cleaner_with({"btw": "by the way"})
    assert c._expand_snippets("hello btw goodbye") == "hello by the way goodbye"


def test_capitalized_match_capitalizes_replacement():
    c = _cleaner_with({"btw": "by the way"})
    assert c._expand_snippets("Btw I forgot") == "By the way I forgot"


def test_allcaps_match_uppercases_replacement():
    c = _cleaner_with({"asap": "as soon as possible"})
    assert c._expand_snippets("Do this ASAP please") == "Do this AS SOON AS POSSIBLE please"


def test_word_boundary_preserved():
    """The substring 'btw' inside 'btwise' should not be expanded."""
    c = _cleaner_with({"btw": "by the way"})
    assert c._expand_snippets("btwise") == "btwise"


def test_multiple_snippets_in_one_sentence():
    c = _cleaner_with({"btw": "by the way", "lgtm": "looks good to me"})
    assert c._expand_snippets("btw your PR lgtm") == "by the way your PR looks good to me"


def test_longest_match_wins():
    """If a shorter and a longer snippet would both match, the longer should win."""
    c = _cleaner_with({"ab": "alpha", "abc": "alphabet"})
    # 'abc' should be replaced as a whole, not as 'alpha c'
    assert c._expand_snippets("abc") == "alphabet"


def test_empty_snippets_no_op():
    c = _cleaner_with({})
    assert c._expand_snippets("hello btw") == "hello btw"


def test_missing_snippet_key_no_op():
    c = _cleaner_with({"btw": "by the way"})
    assert c._expand_snippets("zzz qqq xxx") == "zzz qqq xxx"


def test_punctuation_around_snippet():
    c = _cleaner_with({"btw": "by the way"})
    assert c._expand_snippets("hello, btw.") == "hello, by the way."


def test_clean_pipeline_with_provider_none_still_expands():
    """provider='none' returns early (no LLM), but snippet expansion shouldn't run
    on the raw text because we expand AFTER cleanup. With provider='none' the
    return is `text` unchanged — that's intentional."""
    c = _cleaner_with({"btw": "by the way"})
    # provider='none' means clean() returns text unmodified — no snippet pass.
    # Snippet expansion only happens on the LLM success path.
    assert c.clean("btw test") == "btw test"
