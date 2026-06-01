"""Regression: _parse_combo must raise ValueError (not AttributeError) for
out-of-range function keys like f0/f25/f99, so callers catching ValueError to
show a friendly 'bad hotkey' message work and the daemon doesn't crash."""
from __future__ import annotations

import pytest

from src.hotkey import _parse_combo


@pytest.mark.parametrize("combo", ["f0", "f25", "f99", "ctrl+alt+f99"])
def test_out_of_range_function_keys_raise_valueerror(combo):
    with pytest.raises(ValueError):
        _parse_combo(combo)


@pytest.mark.parametrize("combo", ["f1", "ctrl+f5", "alt+space"])
def test_valid_combos_parse(combo):
    keys = _parse_combo(combo)
    assert isinstance(keys, set) and keys
