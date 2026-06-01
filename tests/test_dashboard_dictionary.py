"""Phase 3 acceptance tests — Dictionary CRUD + config_writer."""
from __future__ import annotations

import types
import pytest

from src.history import History
from src.dashboard import vocabulary as vocab
from src.dashboard import config_writer as cw


# --- vocabulary CRUD ---------------------------------------------------------

def _h(tmp_path):
    return History(str(tmp_path / "h.db"))


def test_add_term_dedupes(tmp_path):
    h = _h(tmp_path)
    a = vocab.add_term(h.conn, "FastAPI")
    b = vocab.add_term(h.conn, "FastAPI")
    assert a == b
    assert len(vocab.list_terms(h.conn)) == 1


def test_add_rejects_empty(tmp_path):
    h = _h(tmp_path)
    with pytest.raises(ValueError):
        vocab.add_term(h.conn, "   ")


def test_add_rejects_too_long(tmp_path):
    h = _h(tmp_path)
    with pytest.raises(ValueError):
        vocab.add_term(h.conn, "x" * 81)


def test_delete_term(tmp_path):
    h = _h(tmp_path)
    tid = vocab.add_term(h.conn, "Supabase")
    assert vocab.delete_term(h.conn, tid) is True
    assert vocab.list_terms(h.conn) == []
    assert vocab.delete_term(h.conn, 9999) is False


def test_bulk_import_mixed_separators(tmp_path):
    h = _h(tmp_path)
    raw = "FastAPI, Supabase\nnode2vec,PostgreSQL\nFastAPI"
    result = vocab.bulk_import(h.conn, raw)
    assert result["added"] == 4
    assert result["duplicates"] == 1  # second FastAPI
    terms = [t["term"] for t in vocab.list_terms(h.conn)]
    assert "FastAPI" in terms
    assert "node2vec" in terms


def test_bulk_import_counts_preexisting_db_term_as_duplicate(tmp_path):
    """A term already in the DB (but not repeated in the paste batch) must be
    counted as a duplicate, not an addition — add_term returns the existing id
    so it cannot be distinguished by id alone."""
    h = _h(tmp_path)
    vocab.add_term(h.conn, "Supabase")  # pre-existing in the DB
    result = vocab.bulk_import(h.conn, "Supabase\nKafka")
    assert result["added"] == 1        # only Kafka is new
    assert result["duplicates"] == 1   # Supabase already existed
    terms = [t["term"] for t in vocab.list_terms(h.conn)]
    assert sorted(t for t in terms if t in {"Supabase", "Kafka"}) == ["Kafka", "Supabase"]


def test_list_terms_alphabetical_case_insensitive(tmp_path):
    h = _h(tmp_path)
    vocab.add_term(h.conn, "zoo")
    vocab.add_term(h.conn, "Apple")
    vocab.add_term(h.conn, "banana")
    terms = [t["term"] for t in vocab.list_terms(h.conn)]
    assert terms == ["Apple", "banana", "zoo"]


def test_all_terms_returns_plain_strings(tmp_path):
    h = _h(tmp_path)
    vocab.add_term(h.conn, "FastAPI")
    vocab.add_term(h.conn, "Ollama")
    assert sorted(vocab.all_terms(h.conn)) == ["FastAPI", "Ollama"]


# --- Dictionary routes -------------------------------------------------------

class _ReloadableApp:
    def __init__(self, history):
        self.cfg = {"dashboard": {"host": "127.0.0.1", "port": 8766}}
        self.history = history
        self.reload_calls = 0

    def reload_config(self):
        self.reload_calls += 1


def _client(tmp_path):
    from src.dashboard.app import make_app
    h = _h(tmp_path)
    app_ref = _ReloadableApp(h)
    return make_app(app_ref).test_client(), app_ref


def test_dictionary_get_empty(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/dictionary", headers={"Host": "127.0.0.1:8766"})
    assert r.status_code == 200
    assert b"No custom terms yet" in r.data


def test_dictionary_add_post_round_trip(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/dictionary/add", headers={"Host": "127.0.0.1:8766"},
                    data={"term": "FastAPI"})
    assert r.status_code == 302
    # Verify it persists
    r2 = client.get("/dictionary", headers={"Host": "127.0.0.1:8766"})
    assert b"FastAPI" in r2.data
    assert app_ref.reload_calls == 1  # reload triggered


def test_dictionary_add_empty_does_not_reload(tmp_path):
    client, app_ref = _client(tmp_path)
    client.post("/dictionary/add", headers={"Host": "127.0.0.1:8766"}, data={"term": "  "})
    assert app_ref.reload_calls == 0


def test_dictionary_delete_post(tmp_path):
    client, app_ref = _client(tmp_path)
    tid = vocab.add_term(app_ref.history.conn, "Bye")
    client.post("/dictionary/delete", headers={"Host": "127.0.0.1:8766"},
                data={"id": str(tid)})
    assert vocab.list_terms(app_ref.history.conn) == []
    assert app_ref.reload_calls == 1


def test_dictionary_import_post(tmp_path):
    client, app_ref = _client(tmp_path)
    client.post("/dictionary/import", headers={"Host": "127.0.0.1:8766"},
                data={"bulk": "Echo, Flow\nClaude"})
    terms = [t["term"] for t in vocab.list_terms(app_ref.history.conn)]
    assert set(terms) == {"Echo", "Flow", "Claude"}
    assert app_ref.reload_calls == 1


# --- config_writer -----------------------------------------------------------

SAMPLE = """# A test config
foo:
  bar: 1            # original comment on bar
  baz: "hello"
  nested:
    deep: 42
top_other: true
""".strip() + "\n"


def test_set_scalar_preserves_comments(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    cw.set_scalar(p, "foo.bar", 99)
    after = p.read_text(encoding="utf-8")
    assert "# original comment on bar" in after
    assert "# A test config" in after
    assert "bar: 99" in after


def test_set_scalar_string_quoted_when_needed(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    cw.set_scalar(p, "foo.baz", "has: colon")
    text = p.read_text(encoding="utf-8")
    assert 'baz: "has: colon"' in text


def test_set_scalar_bool_lowercase(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    cw.set_scalar(p, "top_other", False)
    assert "top_other: false" in p.read_text(encoding="utf-8")


def test_set_scalar_deeply_nested(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    cw.set_scalar(p, "foo.nested.deep", 7)
    assert "deep: 7" in p.read_text(encoding="utf-8")


def test_set_scalar_missing_key_raises(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    with pytest.raises(cw.ConfigWriteError):
        cw.set_scalar(p, "foo.nope", 1)


def test_set_scalar_atomic_tmp_cleanup(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    cw.set_scalar(p, "foo.bar", 2)
    # .tmp should not linger.
    assert not (tmp_path / "c.yaml.tmp").exists()
