"""Phase 10 — Bulk import via file upload (.txt / .csv) for Dictionary + Snippets."""
from __future__ import annotations

import io

from src.history import History
from src.dashboard import bulk_import as bi
from src.dashboard import vocabulary as voc
from src.dashboard import snippets as snip


def _h(tmp_path):
    return History(str(tmp_path / "h.db"))


# --- Pure helpers ------------------------------------------------------------

def test_merge_text_both():
    assert bi.merge_text("a", "b") == "a\nb"


def test_merge_text_one_side():
    assert bi.merge_text("only", "") == "only"
    assert bi.merge_text("", "upload") == "upload"
    assert bi.merge_text("", "") == ""


def test_csv_to_snippet_lines_translates_commas():
    src = "btw,by the way\nfyi,for your information"
    out = bi.csv_to_snippet_lines(src)
    assert "btw = by the way" in out
    assert "fyi = for your information" in out


def test_csv_to_snippet_lines_preserves_existing_separators():
    src = "btw,by the way\nlgtm = looks good to me\nfyi -> for your information"
    out = bi.csv_to_snippet_lines(src).splitlines()
    assert out[0] == "btw = by the way"
    assert out[1] == "lgtm = looks good to me"
    assert out[2] == "fyi -> for your information"


def test_csv_to_snippet_lines_preserves_invalid_lines():
    # No separator at all → pass through unchanged so the snippets parser
    # can flag it as invalid.
    out = bi.csv_to_snippet_lines("nope")
    assert out == "nope"


def test_read_upload_respects_size_cap(tmp_path):
    big = io.BytesIO(b"x" * (bi.MAX_UPLOAD_BYTES + 10_000))
    class FS:
        filename = "big.txt"
        def read(self, n):
            return big.read(n)
    text = bi.read_upload(FS())
    assert len(text) <= bi.MAX_UPLOAD_BYTES


def test_read_upload_empty_filename_returns_blank():
    class FS:
        filename = ""
        def read(self, n):
            return b"ignored"
    assert bi.read_upload(FS()) == ""


def test_read_upload_invalid_utf8_replaced():
    class FS:
        filename = "weird.txt"
        def read(self, n):
            return b"\xff\xfe ok"
    text = bi.read_upload(FS())
    assert "ok" in text  # tolerant decode succeeded


# --- Routes -----------------------------------------------------------------

class _App:
    def __init__(self, history):
        self.cfg = {"dashboard": {"host": "127.0.0.1", "port": 8766}}
        self.history = history
        self._reload_calls = 0
    def reload_config(self):
        self._reload_calls += 1


HOST = {"Host": "127.0.0.1:8766"}


def _client(tmp_path):
    h = _h(tmp_path)
    app_ref = _App(h)
    from src.dashboard.app import make_app
    return make_app(app_ref).test_client(), app_ref


def test_dictionary_import_via_file_upload(tmp_path):
    client, app_ref = _client(tmp_path)
    data = {"bulk": "", "file": (io.BytesIO(b"FastAPI\nSupabase\nnode2vec"), "vocab.txt")}
    r = client.post("/dictionary/import", headers=HOST, data=data,
                    content_type="multipart/form-data")
    assert r.status_code == 302
    terms = {t["term"] for t in voc.list_terms(app_ref.history.conn)}
    assert {"FastAPI", "Supabase", "node2vec"} <= terms


def test_dictionary_import_paste_plus_upload_merges(tmp_path):
    client, app_ref = _client(tmp_path)
    data = {"bulk": "Pasted", "file": (io.BytesIO(b"Uploaded"), "v.txt")}
    r = client.post("/dictionary/import", headers=HOST, data=data,
                    content_type="multipart/form-data")
    assert r.status_code == 302
    terms = {t["term"] for t in voc.list_terms(app_ref.history.conn)}
    assert "Pasted" in terms and "Uploaded" in terms


def test_dictionary_import_rejects_empty(tmp_path):
    import urllib.parse
    client, _ = _client(tmp_path)
    r = client.post("/dictionary/import", headers=HOST, data={"bulk": ""})
    assert r.status_code == 302
    # Assert on the decoded message, not the encoding (quote_plus uses '+'
    # for spaces; the old bare f-string happened to produce '%20').
    q = urllib.parse.urlparse(r.headers["Location"]).query
    flash = urllib.parse.parse_qs(q).get("flash", [""])[0]
    assert "Nothing to import" in flash


def test_snippets_import_via_csv_upload(tmp_path):
    client, app_ref = _client(tmp_path)
    csv = b"btw,by the way\nfyi,for your information\n"
    data = {"bulk": "", "file": (io.BytesIO(csv), "snips.csv")}
    r = client.post("/snippets/import", headers=HOST, data=data,
                    content_type="multipart/form-data")
    assert r.status_code == 302
    items = {s["code"]: s["expansion"] for s in snip.list_snippets(app_ref.history.conn)}
    assert items["btw"] == "by the way"
    assert items["fyi"] == "for your information"


def test_snippets_import_mixed_paste_and_upload(tmp_path):
    client, app_ref = _client(tmp_path)
    paste = "lgtm = looks good to me"
    upload = b"btw,by the way"
    data = {"bulk": paste, "file": (io.BytesIO(upload), "s.csv")}
    r = client.post("/snippets/import", headers=HOST, data=data,
                    content_type="multipart/form-data")
    assert r.status_code == 302
    items = {s["code"]: s["expansion"] for s in snip.list_snippets(app_ref.history.conn)}
    assert items["lgtm"] == "looks good to me"
    assert items["btw"] == "by the way"


def test_snippets_import_rejects_empty(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/snippets/import", headers=HOST, data={"bulk": ""})
    assert r.status_code == 302
    assert "Nothing%20to%20import" in r.headers["Location"]
