"""Whisper's stock caption artifacts — shared set, and peeling them off a tail."""
from __future__ import annotations

from src.asr_artifacts import HALLUCINATIONS, strip_trailing


# --- the dedup regression guard ---------------------------------------------

def test_set_matches_the_two_copies_it_replaced():
    """`main._do_dictation` and `bridge` each held this literal verbatim.

    If someone edits the shared set, the whole-utterance filters on both the
    desktop and mobile paths change with it — so pin the contents.
    """
    assert HALLUCINATIONS == frozenset({
        "thank you.", "thanks for watching.", "thanks for watching!",
        "you", ".", "thank you", "thanks.", "bye.", "you're welcome.",
        "i'm sorry.", "thank you so much.",
    })


def test_both_call_sites_use_the_shared_set():
    from src import bridge, main
    assert bridge._HALLUCINATIONS is HALLUCINATIONS
    assert main.HALLUCINATIONS is HALLUCINATIONS


# --- strip_trailing ----------------------------------------------------------

def test_strips_a_trailing_thank_you():
    assert (strip_trailing("Turn the volume up and open the settings folder. Thank you.")
            == "Turn the volume up and open the settings folder.")


def test_strips_a_bare_trailing_you():
    assert (strip_trailing("The quick brown fox jumps over the lazy dog you")
            == "The quick brown fox jumps over the lazy dog")


def test_strips_stacked_artifacts():
    assert strip_trailing("Open the door. Thank you. Bye.") == "Open the door."


def test_prefers_the_longest_phrase():
    # "you" is also in the set; peeling it first would strand a stray "Thank".
    assert strip_trailing("Read this. Thank you.") == "Read this."


def test_never_empties_an_all_artifact_utterance():
    # Dropping a whole utterance is main's guard to make (it also weighs
    # duration); here an empty result would invent a blank reading.
    assert strip_trailing("Thank you.") == "Thank you."
    assert strip_trailing("you") == "you"


def test_leaves_clean_text_alone():
    text = "Johnson reviewed the PostgreSQL migration and approved it."
    assert strip_trailing(text) == text


def test_handles_empty_input():
    assert strip_trailing("") == ""
    assert strip_trailing(None) == ""
