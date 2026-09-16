"""Guards for the GitHub Pages landing page (docs/index.html).

The page promises "no CDN, no telemetry" and until now shipped with zero
validation. These tests keep the promises checkable without adding a Node
toolchain: nothing loads from a third party, the metadata search engines and
link previews depend on stays intact, hard-coded facts stay honest, the text
colours stay readable, and the hero never hides behind an animation.
"""
from __future__ import annotations

import gzip
import json
import re
import struct
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"
PAGE = DOCS / "index.html"
SITE_URL = "https://johnsonkc201.github.io/Echo_FLOW/"

# Performance budget. The page is one file with no subresources, so this is
# the whole transfer: 45 KB raw, 14 KB gzipped, both well under one round of
# TCP slow-start on GitHub Pages.
RAW_BUDGET = 45_000
GZIP_BUDGET = 14_000

# WCAG 2.2 AA for body text.
MIN_CONTRAST = 4.5

# Tags whose src/href the browser fetches while rendering the page.
SUBRESOURCE_ATTRS = {
    "script": "src",
    "link": "href",
    "img": "src",
    "iframe": "src",
    "video": "src",
    "audio": "src",
    "source": "src",
    "object": "data",
    "embed": "src",
}


class _Page(HTMLParser):
    """Collect the tags, attributes, ids and JSON-LD blocks of one page."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.ids: set[str] = set()
        self.ld_json: list[str] = []
        self.h1_count = 0
        self._in_ld = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        self.tags.append((tag, attr))
        if attr.get("id"):
            self.ids.add(attr["id"])
        if tag == "h1":
            self.h1_count += 1
        self._in_ld = tag == "script" and attr.get("type") == "application/ld+json"

    def handle_data(self, data: str) -> None:
        if self._in_ld:
            self.ld_json.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_ld = False

    def find(self, tag: str, **match: str) -> list[dict[str, str | None]]:
        return [a for t, a in self.tags if t == tag and all(a.get(k) == v for k, v in match.items())]


@pytest.fixture(scope="module")
def html() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def page(html: str) -> _Page:
    parser = _Page()
    parser.feed(html)
    return parser


def _is_local(url: str | None) -> bool:
    if not url:
        return True
    lowered = url.lower()
    if lowered.startswith("data:"):
        return True
    return not (lowered.startswith("http") or lowered.startswith("//"))


# --- The "no CDN" promise ----------------------------------------------------

def test_no_third_party_subresources(page: _Page) -> None:
    offenders = [
        f"<{tag} {attr}={value!r}>"
        for tag, attrs in page.tags
        if (attr := SUBRESOURCE_ATTRS.get(tag))
        and not (tag == "link" and attrs.get("rel") == "canonical")
        and not _is_local(value := attrs.get(attr))
    ]
    assert not offenders, f"the page must not fetch from a third party: {offenders}"


def test_inline_css_has_no_remote_urls(html: str) -> None:
    style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    assert "url(" not in style, "inline CSS must not reference remote assets"


def test_local_subresources_exist(page: _Page) -> None:
    for tag, attrs in page.tags:
        attr = SUBRESOURCE_ATTRS.get(tag)
        value = attrs.get(attr) if attr else None
        if not value or not _is_local(value) or value.startswith("data:"):
            continue
        assert (DOCS / value).is_file(), f"<{tag}> points at a file missing from docs/: {value}"


def test_every_link_is_https_or_in_page(page: _Page) -> None:
    hrefs = [a["href"] for a in page.find("a") if a.get("href")]
    assert hrefs, "no links found, the parser is broken"
    bad = [h for h in hrefs if not (h.startswith("https://") or h.startswith("#") or h.startswith("mailto:"))]
    assert not bad, f"links must be https, in-page anchors, or mailto: {bad}"


def test_in_page_anchors_resolve(page: _Page) -> None:
    targets = {a["href"][1:] for a in page.find("a") if (a.get("href") or "").startswith("#")}
    missing = targets - page.ids
    assert not missing, f"anchors point at ids that do not exist: {sorted(missing)}"


# --- Metadata that search engines and link previews read ---------------------

def test_head_metadata(page: _Page) -> None:
    def meta(**match: str) -> str | None:
        found = page.find("meta", **match)
        return found[0].get("content") if found else None

    canonical = page.find("link", rel="canonical")
    assert canonical and canonical[0].get("href") == SITE_URL
    assert meta(property="og:url") == SITE_URL
    assert meta(property="og:type") == "website"
    assert (meta(property="og:image") or "").startswith("https://")
    assert meta(property="og:image:width") == "1280"
    assert meta(property="og:image:height") == "640"
    assert meta(name="twitter:card") == "summary_large_image"
    assert meta(name="color-scheme") == "dark"
    assert meta(name="theme-color")
    assert meta(name="viewport") == "width=device-width, initial-scale=1"
    description = meta(name="description") or ""
    assert 50 <= len(description) <= 160, f"description is {len(description)} chars"


def test_structured_data_is_a_free_software_application(page: _Page) -> None:
    assert len(page.ld_json) == 1, "expected exactly one JSON-LD block"
    data = json.loads(page.ld_json[0])
    assert data["@type"] == "SoftwareApplication"
    assert data["url"] == SITE_URL
    assert data["offers"]["price"] == "0"
    assert data["downloadUrl"].startswith("https://github.com/JOhnsonKC201/Echo_FLOW/releases")
    assert "softwareVersion" not in data, "the version would drift; leave it to the release page"


def test_document_basics(html: str, page: _Page) -> None:
    assert '<html lang="en"' in html
    assert page.h1_count == 1
    assert "<title>" in html and "Echo Flow" in html


def test_touch_icons_have_the_advertised_sizes() -> None:
    for name, size in (("apple-touch-icon.png", 180), ("favicon-32.png", 32)):
        data = (DOCS / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{name} is not a PNG"
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == (size, size), f"{name} is {width}x{height}, expected {size}x{size}"


# --- Honesty and house style ---------------------------------------------------

def test_no_long_dashes(html: str) -> None:
    for dash in (chr(0x2014), chr(0x2013)):
        assert dash not in html, f"long dash U+{ord(dash):04X} found; use plain punctuation"


def test_test_count_claim_is_honest(html: str, request: pytest.FixtureRequest) -> None:
    """The hero shows a test count. It must never overstate the suite and must
    not drift far behind it, otherwise the number is decoration, not a fact."""
    match = re.search(r'id="test-count">(\d+)<', html)
    assert match, "the hero should carry the test count in <span id=\"test-count\">"
    claimed = int(match.group(1))

    collected = len(request.session.items)
    if collected < 1000:
        pytest.skip("only meaningful against a full-suite collection")

    assert_claim_is_honest(claimed, collected)


def assert_claim_is_honest(claimed: int, collected: int) -> None:
    """Decide how far the published number may sit from the real one.

    ``claimed`` is what docs/index.html says; ``collected`` is what pytest
    actually gathered for this run. Raise AssertionError with a message that
    tells the reader which number to put on the page.
    """
    # TODO(human)


# --- Rendering: readable text, hero paints immediately ---------------------------

def _hex_to_rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _luminance(value: str) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _hex_to_rgb(value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    lighter, darker = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _root_vars(html: str) -> dict[str, str]:
    root = re.search(r":root\{(.*?)\}", html, re.S).group(1)
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", root))


def test_text_colours_meet_wcag_aa_on_every_surface(html: str) -> None:
    colours = _root_vars(html)
    text = ("text", "muted", "faint", "green", "green-dim")
    surfaces = ("bg", "bg2", "panel", "panel2", "well", "keycap")
    missing = [name for name in text + surfaces if name not in colours]
    assert not missing, f"expected these tokens in :root: {missing}"
    failures = [
        f"--{fg} on --{bg} is {contrast_ratio(colours[fg], colours[bg]):.2f}:1"
        for fg in text
        for bg in surfaces
        if contrast_ratio(colours[fg], colours[bg]) < MIN_CONTRAST
    ]
    assert not failures, f"text below {MIN_CONTRAST}:1 contrast: {failures}"


def test_hero_text_is_never_hidden_by_the_reveal_animation(html: str) -> None:
    hero = re.search(r'<div class="hero">(.*?)<div class="demo"', html, re.S).group(1)
    assert "reveal" not in hero, "hero text is the LCP element; it must paint on the first frame"


def test_reveal_cannot_strand_content_hidden(html: str) -> None:
    """Entrance motion is a progressive enhancement: the only rule that starts
    a tile transparent lives inside a feature query for scroll-driven
    animations, so a browser that never runs the animation shows the tile."""
    assert "@supports (animation-timeline: view())" in html
    static_hide = re.search(r"\.reveal\{[^}]*opacity:0", html)
    assert static_hide is None, "a static .reveal rule would hide tiles wherever the animation never runs"
    assert 'classList.add("in")' not in html, "reveal must not depend on script"


def test_demo_has_server_rendered_text(html: str) -> None:
    match = re.search(r'<span class="out" id="out">([^<]+)</span>', html)
    assert match and match.group(1).strip(), "the demo must show a sentence before (or without) JS"


def test_page_stays_within_its_performance_budget(html: str) -> None:
    raw = html.encode("utf-8")
    packed = gzip.compress(raw, 9)
    assert len(raw) <= RAW_BUDGET, f"raw page is {len(raw)} bytes, budget {RAW_BUDGET}"
    assert len(packed) <= GZIP_BUDGET, f"gzipped page is {len(packed)} bytes, budget {GZIP_BUDGET}"
