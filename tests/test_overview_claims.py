"""PRODUCT_OVERVIEW.md restates the suite size as a rounded floor ("2,500+").

The landing page hero carries the exact count, and test_landing_page.py keeps
that number honest against the collected suite. This test keeps the overview
in step with the hero, so its figure cannot go stale unnoticed the way "1502"
and then "1900+" did. It only reads two files, so it runs in every lane.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
OVERVIEW = REPO_ROOT / "PRODUCT_OVERVIEW.md"
LANDING = REPO_ROOT / "docs" / "index.html"

# The overview rounds the hero count down to a multiple of this.
ROUND_TO = 100


def expected_floor(hero_count: int) -> int:
    """The figure the overview should print for a given hero count."""
    return hero_count // ROUND_TO * ROUND_TO


def _hero_count() -> int:
    match = re.search(r'id="test-count">(\d+)<', LANDING.read_text(encoding="utf-8"))
    assert match, 'docs/index.html should carry the count in <span id="test-count">'
    return int(match.group(1))


def _overview_floor() -> int:
    text = OVERVIEW.read_text(encoding="utf-8")
    match = re.search(r"\*\*Tests:\*\* ([\d,]+)\+ passing", text)
    assert match, 'PRODUCT_OVERVIEW.md should say "**Tests:** N+ passing"'
    return int(match.group(1).replace(",", ""))


def test_overview_test_count_follows_the_landing_page() -> None:
    hero = _hero_count()
    want = expected_floor(hero)
    got = _overview_floor()
    assert got == want, (
        f'PRODUCT_OVERVIEW.md says "{got:,}+ passing" but the landing page '
        f'counts {hero}; set it to "{want:,}+ passing"'
    )


@pytest.mark.parametrize(
    ("hero_count", "floor"),
    [
        (2507, 2500),
        (2500, 2500),  # an exact hundred stays put
        (2499, 2400),  # never rounds up past the real count
    ],
)
def test_expected_floor(hero_count: int, floor: int) -> None:
    assert expected_floor(hero_count) == floor
