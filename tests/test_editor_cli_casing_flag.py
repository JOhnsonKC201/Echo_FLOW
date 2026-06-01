"""editor_cli threads cleanup.casing.learn_from_edits into the editor subprocess
via --no-learn-casing, so casing learning is controllable end-to-end."""
from __future__ import annotations


def _run_cli(monkeypatch, argv):
    import src.editor_cli as cli
    captured = {}

    def _fake_open(db, row_id=None, learn_casing=True):
        captured["db"] = db
        captured["row_id"] = row_id
        captured["learn_casing"] = learn_casing

    monkeypatch.setattr(cli, "open_editor", _fake_open)
    monkeypatch.setattr("sys.argv", ["src.editor_cli", *argv])
    rc = cli.main()
    return rc, captured


def test_default_learns_casing(monkeypatch):
    rc, cap = _run_cli(monkeypatch, ["db.sqlite", "last"])
    assert rc == 0
    assert cap["learn_casing"] is True
    assert cap["row_id"] is None


def test_no_learn_casing_flag_disables(monkeypatch):
    rc, cap = _run_cli(monkeypatch, ["db.sqlite", "7", "--no-learn-casing"])
    assert rc == 0
    assert cap["learn_casing"] is False
    assert cap["row_id"] == 7  # flag is stripped, positional args still parse


def test_flag_position_independent(monkeypatch):
    # Flag anywhere in argv is honored and removed from positionals.
    rc, cap = _run_cli(monkeypatch, ["db.sqlite", "--no-learn-casing", "last"])
    assert rc == 0
    assert cap["learn_casing"] is False
    assert cap["db"] == "db.sqlite"
