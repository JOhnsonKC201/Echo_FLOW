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
_RE_OPEN = re.compile(r"^open\s+(.+)$", re.I)

# A bare domain like "github.com" or "docs.python.org/3/" — no scheme, no
# spaces, no shell metacharacters (those are excluded by the char class).
_RE_DOMAIN = re.compile(r"^[\w-]+(\.[\w-]+)+(/\S*)?$")

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
    if not url or any(c in _URL_FORBIDDEN for c in url):
        return False
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    return parsed.scheme in ("http", "https", "mailto")


# --- Handlers ----------------------------------------------------------------
# Each handler signature is (args: dict, ctx: ActionContext) -> (ok, message).
# None of them raise.

def _h_open_url(args: dict, ctx: ActionContext) -> tuple[bool, str]:
    url = (args or {}).get("url", "")
    if not _is_safe_url(url):
        return (False, "That isn't a URL I can safely open.")
    try:
        import webbrowser
        if webbrowser.open(url):
            return (True, f"Opened {url}")
        return (False, f"Couldn't open {url}")
    except Exception as e:  # noqa: BLE001 — handlers must never raise
        return (False, f"Couldn't open the browser: {e}")


def _h_web_search(args: dict, ctx: ActionContext) -> tuple[bool, str]:
    query = ((args or {}).get("query") or "").strip()
    if not query:
        return (False, "There was nothing to search for.")
    url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
    try:
        import webbrowser
        if webbrowser.open(url):
            return (True, f"Searching the web for “{query}”.")
        return (False, "Couldn't open the browser.")
    except Exception as e:  # noqa: BLE001
        return (False, f"Couldn't open the browser: {e}")


def _h_open_app(args: dict, ctx: ActionContext) -> tuple[bool, str]:
    app = ((args or {}).get("app") or "").strip().lower()
    apps = (ctx.cfg.get("experimental", {}) or {}).get("action_apps", {}) or {}
    # Case-insensitive key match against the configured allowlist.
    target = None
    for key, val in apps.items():
        if str(key).strip().lower() == app:
            target = str(val).strip()
            break
    if not target:
        return (False, f"I don't have an app called “{app}” configured.")

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
        if sys.platform == "win32":
            try:
                os.startfile(target)  # type: ignore[attr-defined]
                return (True, f"Opened {app}.")
            except Exception as e:  # noqa: BLE001
                return (False, f"Couldn't find {app}: {e}")
        return (False, f"Couldn't find {app} on this system.")
    except Exception as e:  # noqa: BLE001
        return (False, f"Couldn't launch {app}: {e}")


_HANDLERS: dict[str, Handler] = {
    "open_url": _h_open_url,
    "web_search": _h_web_search,
    "open_app": _h_open_app,
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


def list_supported(cfg: dict) -> list[str]:
    """For the dashboard's experimental panel."""
    labels = [
        "Open email",
        "Open an app  (“open <name>”)",
        "Open a website  (“open <site>” / “go to <site>”)",
        "Search the web  (“search the web for <query>”)",
    ]
    apps = sorted((cfg.get("experimental", {}) or {}).get("action_apps", {}) or {})
    if apps:
        labels.append("Configured apps: " + ", ".join(apps))
    return labels
