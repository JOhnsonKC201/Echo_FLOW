"""Phase 14 — Action Mode: semantic voice actions that reach outside the keyboard.

Sibling to Phase 13 Command Mode (`src/commands.py`). Command Mode turns a
prefixed dictation into an allowlisted *keystroke* ("computer, select all" →
Ctrl+A). Action Mode turns a prefixed dictation into a registered *handler*
("computer, open spotify" → launch the configured app; "computer, search the
web for cats" → open a search URL).

Both share the `"computer"` prefix (`experimental.command_prefix`). The caller
(`main._do_dictation`) runs Command Mode first and only falls through to Action
Mode on a no-match, so keystrokes stay higher-precision.

Safety model (mirrors and extends commands.py's allowlist discipline):
  - Off by default (`experimental.action_mode: false`).
  - Prefix-gated — plain dictation can never become an action.
  - `open_app` resolves against the configured `action_apps` map only; spoken
    text is never handed to a shell. Launches use list-form subprocess (no
    `shell=True`) or `os.startfile`.
  - `open_url` accepts only http/https/mailto and rejects shell metacharacters.
  - Handlers never raise — they catch internally and return (False, message)
    so the caller can notify cleanly. Nothing here deletes, sends, or pays.
"""
from __future__ import annotations

import os
import re
import sys
import urllib.parse
from dataclasses import dataclass
from typing import Callable

from . import commands as _commands


# A handler takes validated args + a context and performs the side effect.
# It returns (ok, human_message). It must NOT raise.
Handler = Callable[[dict, "ActionContext"], "tuple[bool, str]"]


@dataclass(frozen=True)
class ActionMatch:
    name: str            # stable handler id, e.g. "open_app"
    label: str           # human label, e.g. "Open spotify"
    args: dict           # validated args for the handler


@dataclass
class ActionContext:
    """What handlers need without importing the whole App.

    `focused_path` is the best-effort path of the foreground document; it is
    always None in PR 1 (focused_document_path() ships with the summarizer in
    PR 2) and handlers must tolerate that.
    """
    focused_title: str | None
    focused_path: str | None
    cfg: dict
    notify: Callable
    cleaner: object | None = None
    history: object | None = None
    injector: object | None = None   # for keystroke-based handlers (media/volume)


# --- Prefix stripping (parity with commands.strip_prefix) --------------------

def strip_prefix(text: str, prefix_word: str = "computer") -> str | None:
    """Delegate to commands.strip_prefix so Command and Action Mode share one
    prefix grammar exactly. Returns the remainder after the prefix, or None."""
    return _commands.strip_prefix(text, prefix_word)


# --- Action table ------------------------------------------------------------
# Anchored (^) like commands.py. classify() walks these in order; first match
# wins, so more specific patterns (email) precede the generic "open <x>".

_RE_EMAIL = re.compile(r"^open\s+(?:my\s+)?e-?mail\b", re.I)
_RE_SEARCH = re.compile(
    r"^search\s+(?:(?:on\s+)?google\s+|the\s+web\s+|the\s+internet\s+)?for\s+(.+)$",
    re.I,
)
_RE_GOTO = re.compile(r"^go\s+to\s+(.+)$", re.I)
# PR-2 handlers. These are non-"open" verbs, so their order vs _RE_OPEN (the
# catch-all) is safe; classify() still tries them before _RE_OPEN for clarity.
_RE_SUMMARIZE = re.compile(
    r"^summari[sz]e\s+(?:this|the)\s+(?:pdf|document|page|file|doc)\b", re.I
)
_RE_EVENT = re.compile(
    r"^(?:create|add|make|schedule)\s+(?:an?\s+)?(?:calendar\s+)?"
    r"(?:event|meeting|appointment)\s+"
    r"(?:for\s+|about\s+|called\s+|titled\s+)?(.+)$", re.I
)
_RE_NOTE = re.compile(
    r"^(?:take|make|add|create|write)\s+(?:a\s+|me\s+a\s+)?"
    r"note(?:\s+(?:that|saying|about))?[:\s]+(.+)$", re.I
)
# PR-4 catalog. Media/volume map to OS media keys via the injector; folder/clip
# are "open …" forms so classify() must try them before the _RE_OPEN catch-all.
_RE_PLAYPAUSE = re.compile(r"^(?:play|pause|resume)(?:\s+(?:music|the\s+music|media|song|playback))?$", re.I)
_RE_NEXT      = re.compile(r"^(?:next|skip)(?:\s+(?:track|song))?$", re.I)
_RE_PREV      = re.compile(r"^(?:previous|prev|last)(?:\s+(?:track|song))?$", re.I)
_RE_MUTE      = re.compile(r"^(?:un)?mute(?:\s+(?:it|sound|the\s+sound|volume))?$", re.I)
_RE_VOLUP     = re.compile(r"^(?:volume\s+up|turn\s+(?:it\s+|the\s+volume\s+)?up|louder)$", re.I)
_RE_VOLDOWN   = re.compile(r"^(?:volume\s+down|turn\s+(?:it\s+|the\s+volume\s+)?down|quieter|lower\s+(?:the\s+)?volume)$", re.I)
_RE_CLIP      = re.compile(r"^open\s+(?:the\s+)?(?:link|url)\s+(?:in\s+|from\s+)?(?:the\s+)?clipboard$|^open\s+clipboard\s+(?:link|url)$", re.I)
_RE_FOLDER    = re.compile(r"^open\s+(?:the\s+)?(.+?)\s+folder$|^open\s+folder\s+(.+)$", re.I)

_RE_OPEN = re.compile(r"^open\s+(.+)$", re.I)

# A bare domain like "github.com" or "docs.python.org/3/". SEC-7: ASCII-only
# (re.ASCII so \w can't match unicode confusables) and anchored to a real TLD
# label (2–24 ASCII letters), so a spoken phrase that merely contains a dot
# ("node.js", "my.report") is NOT silently navigated to.
_RE_DOMAIN = re.compile(
    r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}(/\S*)?$", re.ASCII
)

_DEFAULT_EMAIL_URL = "https://mail.google.com"


def _domain_to_url(token: str) -> str | None:
    """Turn a spoken site into a safe http(s)/mailto URL, or None if it isn't
    obviously a URL. Never guesses a TLD — "open github" stays an app name."""
    t = (token or "").strip().strip('/')
    if not t:
        return None
    if t.lower().startswith(("http://", "https://", "mailto:")):
        return t if _is_safe_url(t) else None
    if _RE_DOMAIN.match(t):
        url = "https://" + t
        return url if _is_safe_url(url) else None
    return None


def classify(body: str, cfg: dict) -> ActionMatch | None:
    """Match a prefix-stripped command body against the action table.

    Returns an ActionMatch with validated args, or None. Note: `open_app` is
    returned even when the app isn't configured — argument *existence* is a
    dispatch-time concern (so the user gets a clear "no such app" message),
    while *shape* validation happens here.
    """
    if not body:
        return None
    b = body.strip().rstrip(" .!?,")
    if not b:
        return None

    if _RE_EMAIL.match(b):
        url = (cfg.get("experimental", {}) or {}).get(
            "action_email_url"
        ) or _DEFAULT_EMAIL_URL
        return ActionMatch("open_url", "Open email", {"url": url})

    m = _RE_SEARCH.match(b)
    if m:
        query = m.group(1).strip()
        if query:
            return ActionMatch(
                "web_search", f"Search the web for “{query}”",
                {"query": query},
            )

    m = _RE_GOTO.match(b)
    if m:
        url = _domain_to_url(m.group(1))
        if url:
            return ActionMatch("open_url", f"Open {url}", {"url": url})
        return None  # "go to <something that isn't a site>" — unknown action

    if _RE_SUMMARIZE.match(b):
        return ActionMatch("summarize_focused",
                           "Summarize the focused document", {})

    m = _RE_EVENT.match(b)
    if m:
        details = m.group(1).strip()
        if details:
            return ActionMatch("draft_event",
                               f"Draft event: {details[:40]}",
                               {"details": details})

    m = _RE_NOTE.match(b)
    if m:
        note_body = m.group(1).strip()
        if note_body:
            return ActionMatch("quick_note", "Take a note", {"body": note_body})

    # PR-4 catalog (media / volume / clipboard / folder).
    if _RE_PLAYPAUSE.match(b):
        return ActionMatch("media_key", "Play / pause", {"key": "playpause"})
    if _RE_NEXT.match(b):
        return ActionMatch("media_key", "Next track", {"key": "nexttrack"})
    if _RE_PREV.match(b):
        return ActionMatch("media_key", "Previous track", {"key": "prevtrack"})
    if _RE_MUTE.match(b):
        return ActionMatch("media_key", "Mute", {"key": "volumemute"})
    if _RE_VOLUP.match(b):
        return ActionMatch("volume", "Volume up", {"dir": "up"})
    if _RE_VOLDOWN.match(b):
        return ActionMatch("volume", "Volume down", {"dir": "down"})
    if _RE_CLIP.match(b):
        return ActionMatch("open_clipboard_link", "Open clipboard link", {})
    m = _RE_FOLDER.match(b)
    if m:
        folder = (m.group(1) or m.group(2) or "").strip()
        if folder:
            return ActionMatch("open_folder", f"Open {folder} folder",
                               {"folder": folder.lower()})

    m = _RE_OPEN.match(b)
    if m:
        arg = m.group(1).strip()
        url = _domain_to_url(arg)
        if url:
            return ActionMatch("open_url", f"Open {url}", {"url": url})
        # Otherwise treat the remainder as an app name. The whole spoken string
        # becomes the lookup key — a key like "spotify && rm -rf /" simply won't
        # exist in action_apps, so dispatch fails without ever touching a shell.
        app = arg.lower()
        if app:
            return ActionMatch("open_app", f"Open {arg}", {"app": app})

    return None


# --- URL safety --------------------------------------------------------------

# Shell metacharacters + whitespace that have no place in a voice-opened URL.
_URL_FORBIDDEN = set(" \t\r\n<>\"'|&;`$\\^(){}[]")


def _is_safe_url(url: str) -> bool:
    """True only for http/https/mailto URLs with no smuggled metacharacters,
    no userinfo spoofing, and no non-ASCII (homograph) hosts.

    SEC-1/SEC-2 hardening: the raw-character blocklist alone is not enough —
    an attacker can percent-encode control chars, hide behind userinfo
    (``google.com@evil.com``), or use IDN-confusable hosts. So we also decode
    before re-checking, reject userinfo, force ASCII hosts, and restrict
    ``mailto:`` to a bare address (no ``?subject=``/``?body=``/``?cc=`` header
    injection).
    """
    if not url or any(c in _URL_FORBIDDEN for c in url):
        return False
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https", "mailto"):
        return False
    # Decode percent-encoding, then re-check for control chars + metacharacters
    # that were hidden as %0a / %3b / %00 / %26 etc.
    decoded = urllib.parse.unquote(url)
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in decoded):
        return False
    if any(c in _URL_FORBIDDEN for c in decoded):
        return False
    if parsed.scheme == "mailto":
        # SEC-2: bare address only — no query parameters of any kind.
        return parsed.query == "" and "?" not in url and "@" in parsed.path
    # http / https
    host = parsed.hostname or ""
    if not host or parsed.username is not None or parsed.password is not None:
        return False   # block userinfo spoofing (google.com@evil.com)
    try:
        host.encode("ascii")   # defeat IDN homographs
    except UnicodeEncodeError:
        return False
    return True


# --- Handlers ----------------------------------------------------------------
# Each handler signature is (args: dict, ctx: ActionContext) -> (ok, message).
# None of them raise.

def _h_open_url(args: dict, ctx: ActionContext) -> tuple[bool, str]:
    url = (args or {}).get("url", "")
    if not _is_safe_url(url):
        return (False, "That isn't a URL I can safely open.")
    try:
        import webbrowser
        if webbrowser.open(url, new=2, autoraise=True):
            return (True, f"Opened {url}")
        return (False, f"Couldn't open {url}")
    except Exception as e:  # noqa: BLE001 — handlers must never raise
        return (False, f"Couldn't open the browser: {e}")


def _h_web_search(args: dict, ctx: ActionContext) -> tuple[bool, str]:
    query = ((args or {}).get("query") or "").strip()
    if not query:
        return (False, "There was nothing to search for.")
    query = query[:512]   # SEC-8: bound the query length
    url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
    try:
        import webbrowser
        if webbrowser.open(url, new=2, autoraise=True):
            return (True, f"Searching the web for “{query}”.")
        return (False, "Couldn't open the browser.")
    except Exception as e:  # noqa: BLE001
        return (False, f"Couldn't open the browser: {e}")


def _h_open_app(args: dict, ctx: ActionContext) -> tuple[bool, str]:
    app = ((args or {}).get("app") or "").strip().lower()
    apps = (ctx.cfg.get("experimental", {}) or {}).get("action_apps", {}) or {}
    # SEC-4: normalize the allowlist once (case-fold + strip). A collision
    # (two keys folding to the same name) is logged so it isn't silent.
    norm: dict[str, str] = {}
    for key, val in apps.items():
        k = str(key).strip().lower()
        if k in norm:
            try:
                ctx.notify("Echo Flow",
                           f"action_apps has a duplicate key “{k}” — using the first.",
                           "warning")
            except Exception:
                pass
            continue
        norm[k] = str(val).strip()
    target = norm.get(app)
    if not target:
        return (False, f"I don't have an app called “{app}” configured.")

    # SEC-4: reject a configured target that smuggles shell syntax / arguments,
    # unless it is a real file on disk. Voice never reaches here, but a bad
    # config value shouldn't become a command line either.
    if not os.path.isfile(target) and (
        any(c in target for c in "&|<>^") or re.search(r"\s/\w", target)
    ):
        return (False, f"The configured target for “{app}” looks unsafe.")

    # A configured target may itself be a URL (e.g. browser → a homepage).
    if _is_safe_url(target):
        try:
            import webbrowser
            if webbrowser.open(target):
                return (True, f"Opened {app}.")
            return (False, f"Couldn't open {app}.")
        except Exception as e:  # noqa: BLE001
            return (False, f"Couldn't open {app}: {e}")

    return _launch_executable(app, target)


def _launch_executable(app: str, target: str) -> tuple[bool, str]:
    """Launch a configured executable. List-form subprocess — never shell=True,
    never the raw spoken string. `target` comes from config, not from voice."""
    import shutil
    import subprocess

    exe = shutil.which(target) or target
    try:
        subprocess.Popen(
            [exe], shell=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return (True, f"Opened {app}.")
    except FileNotFoundError:
        # Windows: fall back to the shell association / Start-menu App Execution
        # Alias (how "spotify" resolves when it isn't a plain PATH executable).
        # SEC-5: only for alias-shaped tokens or an existing file — never for a
        # composed command string like "cmd /c calc".
        alias_ok = bool(re.fullmatch(r"[\w.+-]+(\.exe)?", target))
        path_ok = os.path.isfile(target)
        if sys.platform == "win32" and (alias_ok or path_ok):
            try:
                os.startfile(target)  # type: ignore[attr-defined]
                return (True, f"Opened {app}.")
            except Exception as e:  # noqa: BLE001
                return (False, f"Couldn't find {app}: {e}")
        return (False, f"Couldn't find {app} on this system.")
    except Exception as e:  # noqa: BLE001
        return (False, f"Couldn't launch {app}: {e}")


_SUMMARY_EXTS = {".pdf", ".txt", ".md", ".docx"}


def _extract_text(path: str, ext: str) -> str:
    """Best-effort text extraction. Lazy-imports heavy parsers and returns ""
    (never raises) when a dependency is missing or the file can't be read."""
    try:
        if ext in (".txt", ".md"):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        if ext == ".pdf":
            try:
                import fitz  # PyMuPDF
            except ImportError:
                return ""
            parts = []
            with fitz.open(path) as doc:
                for page in doc:
                    parts.append(page.get_text())
            return "\n".join(parts)
        if ext == ".docx":
            try:
                import docx
            except ImportError:
                return ""
            return "\n".join(p.text for p in docx.Document(path).paragraphs)
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _h_summarize_focused(args: dict, ctx: ActionContext) -> tuple[bool, str]:
    """Summarize the currently focused document via the LOCAL cleaner only.

    Operates on ctx.focused_path (never a spoken path). Reuses the Ollama
    cleanup path with a summarizer system prompt — no cloud call ever.
    """
    path = ctx.focused_path
    if not path or not os.path.isfile(path):
        return (False, "No document I can read is focused.")
    ext = os.path.splitext(path)[1].lower()
    if ext not in _SUMMARY_EXTS:
        return (False, "I can only summarize PDF, txt, md, or docx files.")
    text = _extract_text(path, ext)
    if not text.strip():
        return (False, "I couldn't read any text from that document.")
    cap = int((ctx.cfg.get("experimental", {}) or {}).get(
        "action_summary_max_chars", 6000))
    text = text[:cap]
    cleaner = ctx.cleaner
    if cleaner is None:
        return (False, "The summarizer isn't available right now.")
    sys_prompt = (
        "You are a concise summarizer. Summarize the following document in "
        "3–5 sentences. Output only the summary, no preamble."
    )
    try:
        # provider_override='ollama' pins local; system_prompt_override swaps
        # the cleanup prompt for a summarization prompt.
        summary, skipped = cleaner.clean(
            text, system_prompt_override=sys_prompt, provider_override="ollama")
    except Exception as e:  # noqa: BLE001
        return (False, f"Summarization failed: {e}")
    summary = (summary or "").strip()
    if skipped or not summary:
        return (False, "Couldn't summarize that document right now.")
    if ctx.history is not None:
        try:
            ctx.history.add_note(
                dictation_id=None,
                title=f"Summary: {os.path.basename(path)}",
                description=summary,
            )
        except Exception:  # noqa: BLE001
            pass
    short = summary if len(summary) <= 180 else summary[:177] + "…"
    return (True, short)


def _ics_escape(s: str) -> str:
    return (s.replace("\\", "\\\\").replace(";", "\\;")
             .replace(",", "\\,").replace("\n", "\\n"))


def _h_draft_event(args: dict, ctx: ActionContext) -> tuple[bool, str]:
    """Write a LOCAL .ics draft and open it — never a calendar API. Draft only."""
    import datetime as _dt

    details = ((args or {}).get("details") or "").strip()
    if not details:
        return (False, "What should the event be?")

    when = None
    try:
        import dateparser
        when = dateparser.parse(details, settings={"PREFER_DATES_FROM": "future"})
    except Exception:  # noqa: BLE001 — missing dep or parse failure
        when = None
    used_default = when is None
    if used_default:
        when = (_dt.datetime.now() + _dt.timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0)

    dtstart = when.strftime("%Y%m%dT%H%M%S")
    dtend = (when + _dt.timedelta(hours=1)).strftime("%Y%m%dT%H%M%S")
    stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%SZ")
    uid = f"echoflow-{when.strftime('%Y%m%d%H%M%S')}@local"
    ics = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//Echo Flow//Action Mode//EN", "BEGIN:VEVENT",
        f"UID:{uid}", f"DTSTAMP:{stamp}",
        f"DTSTART:{dtstart}", f"DTEND:{dtend}",
        f"SUMMARY:{_ics_escape(details)}", "END:VEVENT", "END:VCALENDAR",
    ]) + "\r\n"

    # Relative path: frozen builds chdir to USER_ROOT, so don't hardcode a root.
    drafts = os.path.join("data", "drafts")
    try:
        os.makedirs(drafts, exist_ok=True)
        # SEC-3: name the draft from creation time only — never from spoken
        # `details`. Keeps sensitive event text out of the at-rest filename and
        # makes path traversal structurally impossible (no user data in the path).
        created = _dt.datetime.now().strftime("%Y%m%d%H%M%S")
        fname = os.path.join(drafts, f"event-{created}.ics")
        with open(fname, "w", encoding="utf-8") as f:
            f.write(ics)
    except Exception as e:  # noqa: BLE001
        return (False, f"Couldn't write the event draft: {e}")

    if sys.platform == "win32":
        try:
            os.startfile(os.path.abspath(fname))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    note = " (defaulted to tomorrow 9am — couldn't parse a time)" if used_default else ""
    return (True,
            f"Drafted “{details[:40]}” for "
            f"{when.strftime('%a %b %d, %I:%M %p')}{note}.")


def _h_quick_note(args: dict, ctx: ActionContext) -> tuple[bool, str]:
    """Append a note to the local notes store. No network, no filesystem."""
    body = ((args or {}).get("body") or "").strip()
    if not body:
        return (False, "What should the note say?")
    if ctx.history is None:
        return (False, "Notes aren't available right now.")
    try:
        from . import notes as _notes
        title = _notes._auto_title(body)
        ctx.history.add_note(dictation_id=None, title=title, description=body)
    except Exception as e:  # noqa: BLE001
        return (False, f"Couldn't save the note: {e}")
    return (True, f"Noted: {title}")


_MEDIA_LABELS = {
    "playpause": "Toggled play/pause.",
    "nexttrack": "Skipped to the next track.",
    "prevtrack": "Went to the previous track.",
    "volumemute": "Toggled mute.",
}


def _h_media_key(args: dict, ctx: ActionContext) -> tuple[bool, str]:
    """Fire a single OS media key via the injector. Keys are a fixed allowlist,
    never spoken text."""
    key = (args or {}).get("key", "")
    if key not in _MEDIA_LABELS:
        return (False, "Unknown media key.")
    inj = ctx.injector
    if inj is None or not hasattr(inj, "send_key"):
        return (False, "Media keys aren't available right now.")
    ok = bool(inj.send_key(key))
    return (ok, _MEDIA_LABELS[key] if ok else "Couldn't send the media key.")


def _h_volume(args: dict, ctx: ActionContext) -> tuple[bool, str]:
    """Nudge system volume up/down by a fixed number of steps. Step count is
    hardcoded — never parsed from spoken text."""
    direction = (args or {}).get("dir", "")
    key = {"up": "volumeup", "down": "volumedown"}.get(direction)
    if key is None:
        return (False, "Unknown volume direction.")
    inj = ctx.injector
    if inj is None or not hasattr(inj, "send_key"):
        return (False, "Volume control isn't available right now.")
    steps = 3   # fixed; clamp guards against accidental change
    steps = max(1, min(steps, 5))
    ok = True
    for _ in range(steps):
        ok = bool(inj.send_key(key)) and ok
    return (ok, f"Volume {direction}." if ok else "Couldn't change the volume.")


def _h_open_folder(args: dict, ctx: ActionContext) -> tuple[bool, str]:
    """Open a folder from the configured action_folders allowlist. The spoken
    name is a lookup key — voice can never open an arbitrary path."""
    name = ((args or {}).get("folder") or "").strip().lower()
    folders = (ctx.cfg.get("experimental", {}) or {}).get("action_folders", {}) or {}
    target = None
    for key, val in folders.items():
        if str(key).strip().lower() == name:
            target = os.path.expanduser(os.path.expandvars(str(val).strip()))
            break
    if not target:
        return (False, f"I don't have a folder called “{name}” configured.")
    if not os.path.isdir(target):
        return (False, f"That folder doesn't exist: {target}")
    if sys.platform == "win32":
        try:
            os.startfile(target)  # type: ignore[attr-defined]
            return (True, f"Opened the {name} folder.")
        except Exception as e:  # noqa: BLE001
            return (False, f"Couldn't open the folder: {e}")
    return (False, "Opening folders is only supported on Windows.")


def _h_open_clipboard_link(args: dict, ctx: ActionContext) -> tuple[bool, str]:
    """Open the clipboard contents ONLY if they parse as a safe URL."""
    try:
        import pyperclip
        text = (pyperclip.paste() or "").strip()
    except Exception:  # noqa: BLE001
        return (False, "Couldn't read the clipboard.")
    if not text:
        return (False, "The clipboard is empty.")
    url = text if _is_safe_url(text) else _domain_to_url(text)
    if not url or not _is_safe_url(url):
        return (False, "The clipboard doesn't contain a safe link.")
    return _h_open_url({"url": url}, ctx)


_HANDLERS: dict[str, Handler] = {
    "open_url": _h_open_url,
    "web_search": _h_web_search,
    "open_app": _h_open_app,
    "summarize_focused": _h_summarize_focused,
    "draft_event": _h_draft_event,
    "quick_note": _h_quick_note,
    "media_key": _h_media_key,
    "volume": _h_volume,
    "open_folder": _h_open_folder,
    "open_clipboard_link": _h_open_clipboard_link,
}


def dispatch(match: ActionMatch, ctx: ActionContext) -> tuple[bool, str]:
    """Run the matched handler. Never raises."""
    handler = _HANDLERS.get(match.name)
    if handler is None:
        return (False, f"No handler registered for {match.name}.")
    try:
        return handler(match.args, ctx)
    except Exception as e:  # noqa: BLE001 — defense in depth; handlers shouldn't raise
        return (False, f"Action failed: {e}")


def redact_args(name: str, args: dict) -> dict:
    """SEC-3: minimize sensitive fields before they hit the voice_actions log.
    Queries / note bodies / event details become length placeholders; URLs are
    reduced to scheme+host. Used at the dispatch log site unless the user opts
    into verbose logging."""
    a = dict(args or {})
    if name == "web_search" and "query" in a:
        a["query"] = "<redacted len=%d>" % len(str(a["query"]))
    elif name == "open_url" and "url" in a:
        s = urllib.parse.urlsplit(str(a["url"]))
        a["url"] = f"{s.scheme}://{s.hostname or ''}"
    elif name == "quick_note" and "body" in a:
        a["body"] = "<redacted len=%d>" % len(str(a["body"]))
    elif name == "draft_event" and "details" in a:
        a["details"] = "<redacted len=%d>" % len(str(a["details"]))
    return a


def list_supported(cfg: dict) -> list[str]:
    """For the dashboard's experimental panel."""
    labels = [
        "Open email",
        "Open an app  (“open <name>”)",
        "Open a website  (“open <site>” / “go to <site>”)",
        "Search the web  (“search the web for <query>”)",
        "Summarize the focused document  (“summarize this pdf”)",
        "Draft a calendar event  (“create an event …”) — local .ics draft",
        "Take a note  (“take a note that …”)",
        "Media controls  (“play”, “pause”, “next track”, “previous track”)",
        "Volume  (“volume up” / “volume down” / “mute”)",
        "Open a folder  (“open downloads folder”)",
        "Open a clipboard link  (“open the link in the clipboard”)",
    ]
    apps = sorted((cfg.get("experimental", {}) or {}).get("action_apps", {}) or {})
    if apps:
        labels.append("Configured apps: " + ", ".join(apps))
    return labels
