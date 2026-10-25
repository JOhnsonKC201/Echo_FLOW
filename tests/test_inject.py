"""Unit tests for src/inject.py: pasting or typing text at the cursor, firing
single keys and chords, and the best-effort lookup of the focused document.

The clipboard, pyautogui, sleeping and threads are all faked and recorded on
one timeline, so no test touches the real clipboard, sends a real keystroke or
waits on a real timer. Document lookups run against folders under tmp_path.
"""
from __future__ import annotations

import sys
import threading
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

from src import hostos, hotkey, inject

pytestmark = pytest.mark.unit

PASTE_SETTLE_SECONDS = 0.008
RESTORE_DELAY_SECONDS = 0.2
KEY_GRACE_SECONDS = 0.05
TYPE_INTERVAL_SECONDS = 0.005


class FakeClipboard:
    """Stands in for pyperclip: records reads and writes on the shared timeline
    and can be told to fail."""

    def __init__(self, events: list, value: str | None = "ORIGINAL"):
        self.events = events
        self.value = value
        self.copies: list = []
        self.fail_read = False
        self.fail_write = False
        self.restored = threading.Event()

    def paste(self):
        self.events.append(("read",))
        if self.fail_read:
            raise RuntimeError("clipboard locked")
        return self.value

    def copy(self, text):
        if self.fail_write:
            raise RuntimeError("clipboard locked")
        self.events.append(("copy", text))
        self.copies.append(text)
        self.value = text
        if len(self.copies) >= 2:
            self.restored.set()


class Rig:
    """Everything inject.py reaches for outside itself, faked in one place."""

    def __init__(self, mp: pytest.MonkeyPatch):
        self.mp = mp
        self.events: list[tuple] = []
        self.own_flags: list[bool] = []   # was the own-keystroke window open mid-send
        self.fail: set[str] = set()       # pyautogui functions that should raise
        self.threads: list = []           # restore threads captured, not started
        self.chord: tuple[str, str] = ("ctrl", "v")
        self.clip = FakeClipboard(self.events)

        mp.setattr(inject, "pyperclip", self.clip)
        mp.setattr(inject, "time", types.SimpleNamespace(
            sleep=lambda s: self.events.append(("sleep", s))))
        mp.setattr(inject, "own_keystrokes", self._window)
        mp.setattr(hostos, "paste_keys", lambda platform=None: self.chord)
        gui = types.ModuleType("pyautogui")
        for name in ("hotkey", "press", "typewrite"):
            setattr(gui, name, self._sender(name))
        mp.setitem(sys.modules, "pyautogui", gui)

    @contextmanager
    def _window(self):
        self.events.append(("window", "open"))
        try:
            yield
        finally:
            self.events.append(("window", "close"))

    def _sender(self, name: str):
        def send(*args, **kwargs):
            self.own_flags.append(hotkey._is_own(True))
            if name in self.fail:
                raise RuntimeError(f"{name} failed")
            self.events.append((name, args, kwargs))
        return send

    def capture_threads(self) -> None:
        """Hold restore threads instead of starting them, so a test can run
        their bodies in a chosen order."""
        rig = self

        class HeldThread:
            def __init__(self, target=None, daemon=None, **_):
                self.target = target
                self.daemon = daemon

            def start(self):
                rig.threads.append(self)

        self.mp.setattr(threading, "Thread", HeldThread)

    def of(self, kind: str) -> list[tuple]:
        return [e for e in self.events if e[0] == kind]


@pytest.fixture
def rig(monkeypatch):
    return Rig(monkeypatch)


# --- inject(): text shaping and method choice ------------------------------

def test_defaults_paste_with_restore_and_trailing_space():
    inj = inject.Injector()
    assert (inj.method, inj.restore_clipboard, inj.trailing_space) == ("paste", True, True)


@pytest.mark.parametrize("text", ["", None])
def test_inject_ignores_empty_text(rig, text):
    inject.Injector().inject(text)
    assert rig.events == []


@pytest.mark.parametrize("text,pasted", [
    ("hello", "hello "),
    ("hello ", "hello "),
    ("hello\n", "hello\n"),
    ("a b\r\n", "a b\r\n"),
    ("hello\t", "hello\t "),
    (" ", " "),
    ("\n", "\n"),
])
def test_trailing_space_is_added_only_when_text_does_not_end_in_space_or_newline(
        rig, text, pasted):
    inject.Injector(restore_clipboard=False).inject(text)
    assert rig.clip.copies == [pasted]


def test_trailing_space_can_be_turned_off(rig):
    inject.Injector(trailing_space=False, restore_clipboard=False).inject("hello")
    assert rig.clip.copies == ["hello"]


def test_type_method_types_the_text_and_leaves_the_clipboard_alone(rig):
    inject.Injector(method="type").inject("hello")
    assert rig.of("typewrite") == [
        ("typewrite", ("hello ",), {"interval": TYPE_INTERVAL_SECONDS})]
    assert rig.of("hotkey") == []
    assert rig.of("read") == [] and rig.clip.copies == []


@pytest.mark.parametrize("method", ["paste", "bogus", "", "TYPE", None])
def test_anything_but_exactly_type_pastes(rig, method):
    inject.Injector(method=method, restore_clipboard=False).inject("hi")
    assert rig.clip.copies == ["hi "]
    assert rig.of("typewrite") == []


@pytest.mark.parametrize("method", ["paste", "type"])
def test_unicode_and_emoji_reach_the_target_unchanged(rig, method):
    text = "caf" + chr(0xE9) + " " + chr(0x1F600) + " '; DROP TABLE x;--"
    inject.Injector(method=method, restore_clipboard=False).inject(text)
    sent = rig.clip.copies if method == "paste" else [rig.of("typewrite")[0][1][0]]
    assert sent == [text + " "]


def test_large_text_is_pasted_in_one_write(rig):
    text = "x" * 50_000
    inject.Injector(restore_clipboard=False).inject(text)
    assert rig.clip.copies == [text + " "]


# --- _paste(): ordering, restore, failure ----------------------------------

def test_paste_reads_copies_settles_then_sends_the_chord_inside_the_window(rig):
    rig.capture_threads()
    inject.Injector().inject("hello")
    assert rig.events == [
        ("read",),
        ("copy", "hello "),
        ("sleep", PASTE_SETTLE_SECONDS),
        ("window", "open"),
        ("hotkey", ("ctrl", "v"), {"_pause": False}),
        ("window", "close"),
    ]


@pytest.mark.parametrize("chord", [("ctrl", "v"), ("command", "v")])
def test_paste_sends_whatever_chord_the_host_os_asks_for(rig, chord):
    rig.chord = chord
    inject.Injector(restore_clipboard=False).inject("hello")
    assert rig.of("hotkey") == [("hotkey", chord, {"_pause": False})]


def test_previous_clipboard_comes_back_after_a_delay_on_a_daemon_thread(rig):
    rig.capture_threads()
    inject.Injector().inject("hello")
    assert len(rig.threads) == 1 and rig.threads[0].daemon is True
    assert rig.clip.value == "hello "          # still the dictation until the timer fires
    rig.threads[0].target()
    assert rig.events[-2:] == [("sleep", RESTORE_DELAY_SECONDS), ("copy", "ORIGINAL")]
    assert rig.clip.value == "ORIGINAL"


def test_restore_really_runs_on_a_background_thread(rig):
    inject.Injector().inject("hello")
    assert rig.clip.restored.wait(timeout=5)
    assert rig.clip.value == "ORIGINAL"


def test_an_empty_previous_clipboard_is_restored_too(rig):
    rig.capture_threads()
    rig.clip.value = ""
    inject.Injector().inject("hello")
    rig.threads[0].target()
    assert rig.clip.value == ""


def test_restore_disabled_never_reads_the_clipboard_or_starts_a_thread(rig):
    rig.capture_threads()
    inject.Injector(restore_clipboard=False).inject("hello")
    assert rig.of("read") == []
    assert rig.threads == []
    assert rig.clip.value == "hello "


def test_unreadable_clipboard_still_pastes_and_skips_the_restore(rig):
    rig.capture_threads()
    rig.clip.fail_read = True
    inject.Injector().inject("hello")
    assert rig.clip.copies == ["hello "]
    assert rig.of("hotkey") != []
    assert rig.threads == []


def test_clipboard_without_text_is_not_restored(rig):
    rig.capture_threads()
    rig.clip.value = None
    inject.Injector().inject("hello")
    assert rig.of("hotkey") != []
    assert rig.threads == []


def test_a_failing_restore_is_swallowed(rig):
    rig.capture_threads()
    inject.Injector().inject("hello")
    rig.clip.fail_write = True
    rig.threads[0].target()          # must not raise out of the background thread
    assert rig.clip.value == "hello "


def test_clipboard_write_failure_propagates_and_no_keys_are_sent(rig):
    rig.capture_threads()
    rig.clip.fail_write = True
    with pytest.raises(RuntimeError):
        inject.Injector().inject("hello")
    assert rig.of("hotkey") == []
    assert rig.threads == []


@pytest.mark.xfail(strict=True, reason=(
    "BUG: _paste restores the clipboard it saw, not the user's original. Two "
    "injections closer together than the 0.2 s restore delay make the second "
    "one save the first dictation as 'previous', so the user's real clipboard "
    "is lost and the first timer can also restore before the second paste is read."))
def test_overlapping_pastes_leave_the_users_original_clipboard(rig):
    rig.capture_threads()
    inj = inject.Injector()
    inj.inject("first")
    inj.inject("second")             # lands before the first restore has run
    for thread in rig.threads:       # timers fire in start order
        thread.target()
    assert rig.clip.value == "ORIGINAL"


# --- the own-keystroke window ----------------------------------------------

SENDS = [
    pytest.param(lambda: inject.Injector(restore_clipboard=False).inject("hi"), id="paste"),
    pytest.param(lambda: inject.Injector(method="type").inject("hi"), id="type"),
    pytest.param(lambda: inject.Injector().send_key("enter"), id="key"),
    pytest.param(lambda: inject.Injector().send_hotkey("ctrl+c"), id="hotkey"),
]


@pytest.mark.parametrize("send", SENDS)
def test_every_send_happens_while_the_hotkey_listener_ignores_injected_keys(
        rig, monkeypatch, send):
    monkeypatch.setattr(inject, "own_keystrokes", hotkey.own_keystrokes)
    monkeypatch.setattr(hotkey, "_own_keys_until", 0.0)
    send()
    assert rig.own_flags == [True]
    assert hotkey._own_keys_until != float("inf"), "window must close after the send"


@pytest.mark.parametrize("send,fn", [
    pytest.param(SENDS[0].values[0], "hotkey", id="paste"),
    pytest.param(SENDS[1].values[0], "typewrite", id="type"),
])
def test_a_failed_send_still_closes_the_window_and_raises(rig, send, fn):
    rig.fail.add(fn)
    with pytest.raises(RuntimeError):
        send()
    assert rig.of("window") == [("window", "open"), ("window", "close")]


# --- send_key ---------------------------------------------------------------

@pytest.mark.parametrize("key", ["enter", "tab", "escape", "volumemute"])
def test_send_key_waits_a_beat_then_presses_exactly_that_key(rig, key):
    assert inject.Injector().send_key(key) is True
    assert rig.events == [
        ("sleep", KEY_GRACE_SECONDS),
        ("window", "open"),
        ("press", (key,), {}),
        ("window", "close"),
    ]


def test_send_key_reports_failure_instead_of_raising(rig):
    rig.fail.add("press")
    assert inject.Injector().send_key("enter") is False
    assert rig.of("window") == [("window", "open"), ("window", "close")]


def test_send_key_returns_false_when_pyautogui_cannot_be_imported(rig, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyautogui", None)
    assert inject.Injector().send_key("enter") is False
    assert rig.events == []


# --- send_hotkey ------------------------------------------------------------

@pytest.mark.parametrize("combo,parts", [
    ("ctrl+c", ("ctrl", "c")),
    ("Ctrl+Shift+T", ("ctrl", "shift", "t")),
    (" ctrl + v ", ("ctrl", "v")),
    ("ctrl++v", ("ctrl", "v")),
    ("+ctrl+v+", ("ctrl", "v")),
    ("enter", ("enter",)),
    ("CTRL+ALT+DELETE", ("ctrl", "alt", "delete")),
])
def test_send_hotkey_normalizes_the_combo_before_pressing(rig, combo, parts):
    assert inject.Injector().send_hotkey(combo) is True
    assert rig.of("hotkey") == [("hotkey", parts, {})]
    assert rig.events[0] == ("sleep", KEY_GRACE_SECONDS)


@pytest.mark.parametrize("combo", ["", None, "   ", "+", "++ +", " + "])
def test_send_hotkey_refuses_a_combo_with_no_keys_and_sends_nothing(rig, combo):
    assert inject.Injector().send_hotkey(combo) is False
    assert rig.events == []


def test_send_hotkey_reports_failure_instead_of_raising(rig):
    rig.fail.add("hotkey")
    assert inject.Injector().send_hotkey("ctrl+c") is False
    assert rig.of("window") == [("window", "open"), ("window", "close")]


def test_send_hotkey_returns_false_when_pyautogui_cannot_be_imported(rig, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyautogui", None)
    assert inject.Injector().send_hotkey("ctrl+c") is False


# --- focused window title ---------------------------------------------------

def test_focused_title_is_whatever_the_host_os_reports(monkeypatch):
    monkeypatch.setattr(hostos, "frontmost_title", lambda platform=None: "Inbox - Mail")
    assert inject.Injector().focused_title() == "Inbox - Mail"


def test_focused_title_is_empty_when_the_os_will_not_say(monkeypatch):
    monkeypatch.setattr(hostos, "frontmost_title", lambda platform=None: "")
    assert inject.Injector().focused_title() == ""


# --- focused document path --------------------------------------------------

@pytest.fixture
def folders(monkeypatch, tmp_path):
    """cwd and a fake home with the three folders the lookup searches, in order."""
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    found = [cwd, home / "Documents", home / "Desktop", home / "Downloads"]
    for d in found:
        d.mkdir(parents=True)
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("USERPROFILE", str(home))   # Path.home() on Windows
    monkeypatch.setenv("HOME", str(home))          # and everywhere else
    if Path.home().resolve() != home.resolve():
        pytest.skip("Path.home() does not honour the environment on this platform")
    return found


def doc_path(monkeypatch, title: str) -> str | None:
    monkeypatch.setattr(inject, "_focused_window_title", lambda: title)
    return inject.Injector().focused_document_path()


def same_file(result: str | None, expected: Path) -> bool:
    return result is not None and Path(result).resolve() == expected.resolve()


UNICODE_NAME = "r" + chr(0xE9) + "sum" + chr(0xE9) + ".pdf"


@pytest.mark.parametrize("title,name", [
    ("report.pdf - Adobe Acrobat", "report.pdf"),
    ("notes.txt - Notepad", "notes.txt"),
    ("README.md - project - Visual Studio Code", "README.md"),
    ("Quarterly Report (final).docx - Word", "Quarterly Report (final).docx"),
    ("my-file_v2.doc - Word", "my-file_v2.doc"),
    ("v1.2.txt - Notepad", "v1.2.txt"),
    ("UPPER.PDF - Reader", "UPPER.PDF"),
    (UNICODE_NAME + " - Reader", UNICODE_NAME),
])
def test_filename_in_the_title_resolves_to_the_real_file(folders, monkeypatch, title, name):
    target = folders[0] / name
    target.write_text("x")
    assert same_file(doc_path(monkeypatch, title), target)


@pytest.mark.parametrize("first", range(4), ids=["cwd", "Documents", "Desktop", "Downloads"])
def test_folders_are_searched_in_a_fixed_order(folders, monkeypatch, first):
    for d in folders[first:]:
        (d / "plan.txt").write_text("x")
    assert same_file(doc_path(monkeypatch, "plan.txt - Notepad"), folders[first] / "plan.txt")


@pytest.mark.parametrize("title", [
    "",
    "Untitled - Notepad",
    "Google Chrome",
    "notes.xyz - Viewer",
    "readme - Code",
    ".pdf",
    "   ",
])
def test_title_without_a_document_name_resolves_to_nothing(folders, monkeypatch, title):
    (folders[0] / "readme").write_text("x")
    assert doc_path(monkeypatch, title) is None


def test_named_document_that_does_not_exist_resolves_to_nothing(folders, monkeypatch):
    assert doc_path(monkeypatch, "ghost.pdf - Adobe") is None


def test_a_folder_named_like_a_document_is_not_a_document(folders, monkeypatch):
    (folders[0] / "notes.txt").mkdir()
    assert doc_path(monkeypatch, "notes.txt - Notepad") is None


@pytest.mark.parametrize("sep", ["/", "\\"])
def test_path_separators_in_the_title_cannot_steer_the_lookup_elsewhere(
        folders, monkeypatch, tmp_path, sep):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.txt").write_text("x")
    title = f"..{sep}elsewhere{sep}secret.txt - Notepad"
    assert doc_path(monkeypatch, title) is None


def test_an_unreadable_folder_is_skipped_not_fatal(folders, monkeypatch):
    target = folders[1] / "plan.txt"
    target.write_text("x")
    real_is_file = Path.is_file

    def flaky(self):
        if self.parent == Path.cwd():
            raise PermissionError("denied")
        return real_is_file(self)

    with monkeypatch.context() as m:
        m.setattr(Path, "is_file", flaky)
        result = doc_path(monkeypatch, "plan.txt - Notepad")
    assert same_file(result, target)


@pytest.mark.xfail(strict=True, reason=(
    "BUG: _DOC_TOKEN has no boundary after the extension, so 'notes.mdx' or "
    "'budget.docm' match as 'notes.md' / 'budget.doc' and resolve to a different "
    "file that happens to exist in the same folder."))
@pytest.mark.parametrize("title,other", [
    ("notes.mdx - Code", "notes.md"),
    ("budget.docm - Word", "budget.doc"),
])
def test_longer_extension_does_not_resolve_to_a_shorter_sibling(
        folders, monkeypatch, title, other):
    (folders[0] / other).write_text("x")
    assert doc_path(monkeypatch, title) is None


@pytest.mark.xfail(strict=True, reason=(
    "BUG: _focused_document_path documents 'Never raises', but Path.cwd() and "
    "Path.home() are called outside the try block, so a deleted cwd or an "
    "unresolvable home directory raises out of it."))
@pytest.mark.parametrize("probe", ["cwd", "home"])
def test_lookup_never_raises_when_cwd_or_home_is_unavailable(
        folders, monkeypatch, probe):
    def gone(*_a, **_k):
        raise OSError("unavailable")

    with monkeypatch.context() as m:
        m.setattr(Path, probe, gone)
        result = doc_path(monkeypatch, "plan.txt - Notepad")
    assert result is None
