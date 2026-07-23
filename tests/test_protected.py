"""Hard-exclude zones (src/protected.py).

Numbers, hyperparameters, citations, quotes and code are masked so the model
rewrites the prose around them, then restored byte-for-byte. If the model breaks
a placeholder, intact() fails and the caller keeps the original.
"""
from __future__ import annotations

from src import protected as p


def test_masks_the_high_value_spans():
    text = ("We set lr=0.001 and got F1 of 0.79 on the 80/20 split "
            "(Smith et al., 2020), see [3]. It ran in 12.5ms. Code: `train.py`.")
    masked, orig = p.mask(text)
    assert "lr=0.001" in orig
    assert "0.79" in orig
    assert "80/20" in orig
    assert "(Smith et al., 2020)" in orig
    assert "[3]" in orig
    assert "12.5ms" in orig
    assert "`train.py`" in orig
    # None of those literals remain in the masked prose the model would see.
    for span in orig:
        assert span not in masked


def test_roundtrips_through_a_clean_rewrite():
    text = "We used lr=0.001 and hit an F1 of 0.79 on 80/20."
    masked, orig = p.mask(text)
    rewrite = masked.replace("We used", "We ran with").replace("hit an", "got an")
    assert p.intact(rewrite, len(orig))
    assert p.unmask(rewrite, orig) == "We ran with lr=0.001 and got an F1 of 0.79 on 80/20."


def test_tolerates_reorder_and_stray_spaces():
    text = "Accuracy was 0.91 and loss was 0.08."
    masked, orig = p.mask(text)
    # Model reorders the placeholders and inserts spaces — still intact.
    a, b = "⟦0⟧", "⟦1⟧"
    reordered = f"Loss ⟦ 1 ⟧ and accuracy {a} were reported."
    assert p.intact(reordered, len(orig))


def test_intact_fails_when_a_placeholder_is_lost_or_duplicated():
    text = "F1 was 0.79 and recall was 0.66."
    masked, orig = p.mask(text)
    assert not p.intact(masked.replace("⟦1⟧", "roughly right"), len(orig))  # dropped
    assert not p.intact(masked + " ⟦0⟧", len(orig))                          # duplicated


def test_protects_quotes_and_code_and_urls():
    text = 'She said "keep it exact" and see https://x.io/y and run `make test`.'
    masked, orig = p.mask(text)
    assert '"keep it exact"' in orig
    assert "https://x.io/y" in orig
    assert "`make test`" in orig


def test_clean_prose_masks_nothing():
    text = "We shipped the parser and it reads most invoice formats."
    masked, orig = p.mask(text)
    assert orig == [] and masked == text
    assert p.intact(masked, 0)


def test_single_digits_are_left_alone():
    # A lone small integer isn't a hard-exclude span (masking it just fragments
    # the prose); two-plus-digit numbers and decimals are protected.
    _, orig = p.mask("We ran 3 trials and saw 42 failures across 0.5 of them.")
    assert "3" not in orig
    assert "42" in orig and "0.5" in orig


def test_handles_empty():
    assert p.mask("") == ("", [])
    assert p.count("") == 0 and p.find("") == []
