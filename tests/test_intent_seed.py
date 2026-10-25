"""Unit tests for src/intent_seed.py: the shipped training corpus has to stay
consistent with the label spec and with the handlers that can actually run."""
from __future__ import annotations

from collections import Counter

import pytest

from src import intent_seed, voice_actions

pytestmark = pytest.mark.unit

# Enough examples per class that one bad seed cannot steer a label on its own.
MIN_EXAMPLES_PER_LABEL = 10


def test_every_seed_label_is_in_the_spec():
    unknown = {lbl for _, lbl in intent_seed.SEED} - set(intent_seed.LABEL_SPEC)
    assert not unknown


def test_every_spec_label_has_enough_examples():
    counts = Counter(lbl for _, lbl in intent_seed.SEED)
    thin = {lbl: counts[lbl] for lbl in intent_seed.LABEL_SPEC
            if counts[lbl] < MIN_EXAMPLES_PER_LABEL}
    assert not thin


def test_no_utterance_is_labelled_twice():
    counts = Counter(text for text, _ in intent_seed.SEED)
    assert [t for t, n in counts.items() if n > 1] == []


def test_utterances_are_normalized_lowercase_without_punctuation():
    # The classifier embeds lowercased, punctuation-free transcripts, so the
    # seed must look like what it will be compared against.
    for text, _ in intent_seed.SEED:
        assert text == text.strip().lower()
        assert not any(ch in text for ch in ",?!;:")


def test_spec_points_only_at_registered_handlers():
    # "open" is the url-or-app pseudo-handler resolved by intent_model.build_match.
    known = set(voice_actions._HANDLERS) | {"open"}
    for label, spec in intent_seed.LABEL_SPEC.items():
        if spec is None:
            continue
        handler, _ = spec
        assert handler in known, label


def test_fixed_media_slots_are_keys_the_handler_accepts():
    for label, spec in intent_seed.LABEL_SPEC.items():
        if spec and spec[0] == "media_key":
            assert spec[1] in voice_actions._MEDIA_LABELS, label


def test_none_is_the_only_abstain_label():
    assert [k for k, v in intent_seed.LABEL_SPEC.items() if v is None] == ["none"]


def test_labels_are_distinct_and_in_first_seen_order():
    labels = intent_seed.labels()
    assert len(labels) == len(set(labels))
    assert labels[0] == intent_seed.SEED[0][1]
    assert set(labels) == {lbl for _, lbl in intent_seed.SEED}
