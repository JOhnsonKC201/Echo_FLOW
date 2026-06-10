"""Regression: the standalone history viewer must HTML-escape the metadata
fields (window title, lang, style), not just raw/clean. The window title is
attacker-influenced (it's the foreground app's title at dictation time), so an
unescaped ${r.window} interpolated into innerHTML is stored XSS."""
from __future__ import annotations


def _render(tmp_path, window_title):
    from src.history import History
    from src import viewer

    db = str(tmp_path / "hist.db")
    h = History(db)
    h.log(window_title=window_title, style="default", language="en",
          duration_ms=100, raw_text="raw", cleaned_text="clean")
    h.conn.close()
    out = viewer.render_history(db, out_path=str(tmp_path / "history.html"),
                                open_browser=False)
    return open(out, encoding="utf-8").read()


def test_metadata_fields_are_escaped_in_template(tmp_path):
    html = _render(tmp_path, "<img src=x onerror=alert(1)>")
    # The template must route every interpolated metadata field through
    # escapeHtml — no bare ${r.window}/${r.lang}/${r.style} sinks remain.
    assert "${escapeHtml(r.window)}" in html
    assert "${escapeHtml(r.lang)}" in html
    assert "${escapeHtml(r.style)}" in html
    assert "${r.window}" not in html
    assert "${r.lang}" not in html
    assert "${r.style}" not in html


def test_data_json_cannot_break_out_of_script_block(tmp_path):
    """Regression: __DATA__ is embedded in an inline <script> block via
    json.dumps, which does not escape '</script>'. Before the fix, dictated
    text containing a literal '</script>' closed the block and injected live
    markup into history.html (stored XSS). The JSON now escapes '</'."""
    payload = "</script><script>alert(1)</script>"
    html = _render(tmp_path, payload)
    assert payload not in html
    assert "<\\/script>" in html  # data survives with the JS-safe escape
