"""The standalone graph report (data/graph.html, opened from the tray) embeds
the user's dictation text, so it must not pull any script from the network:
a tampered CDN copy could read the page and send that text away."""
from __future__ import annotations

import re

import pytest

from tests.test_graph_notes_render import _render

pytestmark = pytest.mark.unit


def test_report_loads_no_remote_script(tmp_path):
    html = _render(tmp_path, "a note")
    srcs = re.findall(r"<script[^>]*\bsrc=\"([^\"]+)\"", html)
    assert srcs == []


def test_report_inlines_the_vendored_d3(tmp_path):
    html = _render(tmp_path, "a note")
    assert "// https://d3js.org v7." in html


def test_inlined_d3_cannot_close_its_own_script_block(tmp_path):
    html = _render(tmp_path, "a note")
    start = html.index("// https://d3js.org v7.")
    end = html.index("</script>", start)
    # The first </script> after D3 starts must be the end of the D3 block,
    # i.e. far past the library, not a stray sequence inside it.
    assert end - start > 200_000


def test_a_closing_script_tag_inside_the_library_is_neutralized(tmp_path, monkeypatch):
    from src import graph
    fake = tmp_path / "d3.js"
    fake.write_text('var s = "</script><SCRIPT>";', encoding="utf-8")
    monkeypatch.setattr(graph, "_D3_PATH", fake)
    out = graph._inline_d3()
    assert "</script" not in out.lower()
    assert out == 'var s = "<' + chr(92) + '/script><SCRIPT>";'


@pytest.mark.parametrize("title", ["__D3_JS__", "__TS_MIN__ and __DICT_DATA__"])
def test_placeholder_text_in_a_dictation_is_not_substituted(tmp_path, title):
    html = _render(tmp_path, title)
    assert html.count("// https://d3js.org v7.") == 1
    assert title in html
