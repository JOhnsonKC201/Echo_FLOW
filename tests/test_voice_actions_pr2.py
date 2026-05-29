"""Phase 14 — PR-2 deferred handlers: summarize_focused, draft_event,
quick_note, and the focused_document_path() injector helper."""
from __future__ import annotations

import pytest

from src import voice_actions as va


def _cfg():
    return {"experimental": {"action_mode": True}}


def _ctx(**kw):
    base = dict(focused_title=None, focused_path=None, cfg=_cfg(),
               notify=lambda *a, **k: None)
    base.update(kw)
    return va.ActionContext(**base)


# --- classify: new verbs ----------------------------------------------------

@pytest.mark.parametrize("body,name", [
    ("summarize this pdf", "summarize_focused"),
    ("summarise the document", "summarize_focused"),
    ("summarize the page", "summarize_focused"),
    ("create an event lunch with sam tomorrow", "draft_event"),
    ("schedule a meeting standup at 10am", "draft_event"),
    ("add a calendar appointment dentist friday", "draft_event"),
    ("take a note that the build is green", "quick_note"),
    ("make a note: call the dentist", "quick_note"),
    ("write a note about the refactor", "quick_note"),
])
def test_classify_pr2_verbs(body, name):
    m = va.classify(body, _cfg())
    assert m is not None, body
    assert m.name == name


def test_event_and_note_dont_shadow_open():
    # "open notepad" is still open_app, unaffected by the new verbs.
    m = va.classify("open notepad", _cfg())
    assert m is not None and m.name == "open_app"


# --- quick_note dispatch ----------------------------------------------------

def test_quick_note_persists(temp_db):
    history, _ = temp_db
    ctx = _ctx(history=history)
    ok, msg = va.dispatch(va.ActionMatch("quick_note", "Take a note",
                                         {"body": "buy milk"}), ctx)
    assert ok is True
    notes = history.list_notes()
    assert any("buy milk" in (n[3] or "") for n in notes)


def test_quick_note_empty_body_fails():
    ok, msg = va.dispatch(va.ActionMatch("quick_note", "Take a note", {"body": ""}), _ctx())
    assert ok is False


# --- draft_event dispatch ---------------------------------------------------

def test_draft_event_writes_local_ics(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("os.startfile", lambda *a, **k: None, raising=False)
    ctx = _ctx()
    ok, msg = va.dispatch(va.ActionMatch("draft_event", "e",
                                         {"details": "lunch with sam"}), ctx)
    assert ok is True
    files = list((tmp_path / "data" / "drafts").glob("*.ics"))
    assert files, "expected an .ics draft to be written"
    content = files[0].read_text(encoding="utf-8")
    assert "BEGIN:VCALENDAR" in content
    assert "SUMMARY:lunch with sam" in content


# --- summarize_focused dispatch ---------------------------------------------

def test_summarize_no_focus_fails():
    ok, msg = va.dispatch(va.ActionMatch("summarize_focused", "s", {}), _ctx())
    assert ok is False
    assert "focused" in msg.lower()


def test_summarize_txt_uses_local_cleaner(tmp_path):
    p = tmp_path / "doc.txt"
    p.write_text("A long document about quarterly results and next steps.")

    seen = {}

    class FakeCleaner:
        def clean(self, text, **kw):
            seen["provider"] = kw.get("provider_override")
            seen["sys"] = kw.get("system_prompt_override")
            return ("This is the summary.", False)

    ctx = _ctx(focused_path=str(p), cleaner=FakeCleaner())
    ok, msg = va.dispatch(va.ActionMatch("summarize_focused", "s", {}), ctx)
    assert ok is True
    assert "summary" in msg.lower()
    assert seen["provider"] == "ollama"   # pinned local, never cloud
    assert seen["sys"]                      # summarizer prompt was injected


def test_summarize_skipped_degrades(tmp_path):
    p = tmp_path / "doc.md"
    p.write_text("# Title\n\nSome content.")

    class SkipCleaner:
        def clean(self, text, **kw):
            return ("", True)   # fast-path skipped the LLM

    ctx = _ctx(focused_path=str(p), cleaner=SkipCleaner())
    ok, msg = va.dispatch(va.ActionMatch("summarize_focused", "s", {}), ctx)
    assert ok is False


def test_summarize_unsupported_ext_fails(tmp_path):
    p = tmp_path / "image.png"
    p.write_text("not really a png")
    ctx = _ctx(focused_path=str(p))
    ok, msg = va.dispatch(va.ActionMatch("summarize_focused", "s", {}), ctx)
    assert ok is False


# --- focused_document_path() injector helper --------------------------------

def test_focused_document_path_none_when_no_title(monkeypatch):
    from src import inject
    monkeypatch.setattr(inject, "_focused_window_title", lambda: "")
    assert inject._focused_document_path() is None


def test_focused_document_path_resolves(tmp_path, monkeypatch):
    from src import inject
    (tmp_path / "report.pdf").write_text("x")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(inject, "_focused_window_title",
                        lambda: "report.pdf - Adobe Acrobat Reader")
    assert inject._focused_document_path() == str(tmp_path / "report.pdf")


def test_focused_document_path_no_match_returns_none(monkeypatch):
    from src import inject
    monkeypatch.setattr(inject, "_focused_window_title",
                        lambda: "Slack | general | Acme")
    assert inject._focused_document_path() is None
