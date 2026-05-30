# Echo Flow — Phase 14: Voice Action Layer (Spec)

> **Hand this file to Claude Code in VS Code.** It is grounded in the real
> Echo Flow architecture as of 2026-05-29. Read the referenced files before
> writing code. Do NOT redesign the pipeline — extend it at the documented
> seam.
>
> **Decisions already made (do not re-litigate):**
> 1. **Shared `"computer"` prefix with fallthrough.** Command Mode runs first;
>    on no-match, fall through to Action Mode. See §3.4. This requires the small
>    refactor of the Phase 13 block described there — do it carefully and keep
>    flag-off behavior byte-identical.
> 2. **First PR ships the safe trio only:** `open_app`, `open_url`,
>    `web_search`. `summarize_focused`, `draft_event`, and `quick_note` are
>    deferred to a second PR. `focused_document_path()` (§3.5) is therefore NOT
>    needed in PR 1 — defer it with the summarizer.

---

## 0. One-paragraph goal

Echo Flow already turns speech into cleaned text and (optionally) into
**keystrokes** via Phase 13 Command Mode (`src/commands.py`). This phase adds a
new, higher-level class of voice command: **semantic actions that reach outside
the keyboard** — "open my email", "summarize the PDF on screen", "create a
calendar event for 3pm tomorrow", "open Spotify", "search Google for X". These
route to Python handlers (launch app, open URL, run a local summarizer, etc.),
not to `pyautogui` keystrokes.

This is **Phase 14: Action Mode**. It lives alongside Command Mode, not inside
it. Command Mode stays exactly as-is.

---

## 1. Required reading (in this order)

1. `src/commands.py` — the Phase 13 regex-first classifier + safety model. Copy
   its **structure and discipline** (allowlist, regex table, `classify()`
   returning a typed tuple, `list_supported()` for the dashboard).
2. `src/main.py` lines **543–745** (`_do_dictation`) — the live pipeline. The
   Command Mode block is at **707–744**. Action Mode hooks in **right here**,
   immediately after the Command Mode block and before scratchpad/inject.
3. `src/actions.py` — note this file is **passive TODO extraction**, unrelated
   to "actions" in this spec. Do **not** overload it. New code goes in a new
   module `src/voice_actions.py` (named to avoid the collision).
4. `config.yaml` lines **186–189** — the `experimental:` block where the feature
   flag goes.
5. `src/inject.py`, `src/notify.py`, `src/history.py` — for the injector,
   `wnotify.notify(...)`, and how `log_command` is implemented (mirror it for
   `log_action`).

---

## 2. Non-negotiable design constraints (match the existing codebase)

- **Local-first.** Echo Flow's identity is local-only. App launch, URL open,
  file ops, and local summarization need **no cloud**. Any handler that *would*
  need a cloud call (e.g. LLM summarization beyond the local Ollama model) must
  be opt-in and use the existing provider routing in `src/cleanup.py` — never a
  new direct API call.
- **Off by default.** New flag `experimental.action_mode: false`.
- **Prefix-gated, like Command Mode.** Reuse the same prefix word
  (`experimental.command_prefix`, default `"computer"`). A dictation only enters
  Action Mode if it starts with the prefix. Plain dictation is never an action.
- **Allowlist mindset, but extended.** Phase 13 forbids arbitrary keystrokes.
  Phase 14 introduces side-effecting handlers, so safety shifts from "allowlist
  of keys" to **"allowlist of registered handlers + argument validation"**:
  - No handler may execute an arbitrary shell string from voice.
  - App launch resolves against a **configured app map**
    (`experimental.action_apps` in config.yaml), not a free-form path.
  - URL open is restricted to `http`/`https`/`mailto` schemes.
  - File-targeting handlers ("summarize this PDF") operate on the **currently
    focused document only** (resolved via the OS/Win32), never an arbitrary path
    spoken aloud.
- **Never silently destructive.** No handler may delete, overwrite, send, or
  pay. "Create a calendar event" produces a **draft** (e.g. opens the calendar
  compose UI / writes an `.ics` to a drafts folder), it does not silently commit.
- **Always returns before inject.** Like Command Mode, a recognized action must
  `return` out of `_do_dictation` so no text gets pasted behind it.
- **Everything logged.** Mirror `history.log_command` with `history.log_action`
  (new table `voice_actions`) capturing: body, handler name, args (JSON),
  ok/fail, error string. The dashboard will read this later.
- **No new always-on dependencies.** Use stdlib (`webbrowser`, `subprocess`,
  `os.startfile`, `shutil.which`) wherever possible. If a handler needs an extra
  package, import it lazily inside the handler and degrade gracefully with a
  `wnotify.notify(..., "warning")` if it's missing.

---

## 3. Architecture

### 3.1 New module: `src/voice_actions.py`

Mirror `commands.py`. Public surface:

```python
# A handler takes parsed args and performs the side effect.
# It returns (ok: bool, human_message: str). It must NOT raise; catch
# internally and return (False, "...") so the caller can notify cleanly.
Handler = Callable[[dict], tuple[bool, str]]

@dataclass(frozen=True)
class ActionMatch:
    name: str            # stable handler id, e.g. "open_app"
    label: str           # human label, e.g. "Open Spotify"
    args: dict           # validated args for the handler

def classify(body: str, cfg: dict) -> ActionMatch | None:
    """Prefix already stripped by caller. Match `body` against the action
    table. Return an ActionMatch (with validated args) or None."""

def dispatch(match: ActionMatch, ctx: "ActionContext") -> tuple[bool, str]:
    """Run the matched handler. Never raises."""

def list_supported(cfg: dict) -> list[str]:
    """For the dashboard experimental panel."""
```

`ActionContext` is a tiny dataclass carrying what handlers need without
importing `App`: `focused_title: str | None`, `focused_path: str | None`
(best-effort path of the foreground document, may be None), `cfg: dict`,
`notify: Callable`, `cleaner` (for local summarization), `history`.

### 3.2 Action table (regex → handler), v1 scope

Implement these handlers. Keep the regex anchored (`^`) like `commands.py`:

| Spoken (after prefix)                    | Handler        | Behavior                                                                 |
|------------------------------------------|----------------|--------------------------------------------------------------------------|
| "open email" / "open my email"           | `open_url`     | Opens configured mail URL (default `https://mail.google.com`) or `mailto:` |
| "open <appname>"                         | `open_app`     | Looks up `<appname>` in `action_apps` map; `subprocess`/`os.startfile`   |
| "open <site>" / "go to <site>"           | `open_url`     | Opens `https://<site>` if it parses as a domain                          |
| "search (google\|the web) for <query>"   | `web_search`   | Opens `https://www.google.com/search?q=<query>` (urlencoded)             |
| "summarize (this\|the) (pdf\|document\|page)" | `summarize_focused` | Resolve focused doc → extract text → local Ollama summary → show in editor/notify |
| "create a (calendar )?event <details>"   | `draft_event`  | Build an `.ics` draft in `data/drafts/` and `os.startfile` it (opens default calendar). **Draft only.** |
| "take a note <body>"                     | `quick_note`   | Append to the Notes store (reuse `src/notes.py` if present) — no network  |

> Start with `open_app`, `open_url`, `web_search` (pure stdlib, zero risk).
> Then `summarize_focused` (reuses `self.cleaner`). Then `draft_event` /
> `quick_note`. Land them incrementally; each should be independently testable.

### 3.3 Argument validation rules

- `open_app`: `<appname>` lowercased, must be a **key in `action_apps`**. If not
  found → `(False, "I don't have an app called X configured.")` + notify. Never
  pass spoken text to a shell.
- `open_url`: scheme must be in `{http, https, mailto}`. Build domains via
  `urllib.parse`; reject anything with spaces or shell metacharacters.
- `web_search`: `urllib.parse.quote_plus` the query. Always `https`.
- `summarize_focused`: if `focused_path` is None or not a readable
  `.pdf/.txt/.md/.docx` → notify "No document I can read is focused." For PDF,
  lazy-import `pymupdf` (already used elsewhere in your work — confirm it's in
  `requirements.txt`; if not, degrade gracefully).
- `draft_event`: parse date/time best-effort (lazy-import `dateparser` if
  available; otherwise default to "tomorrow 9am" and say so). Write `.ics`,
  open it. Never hit a calendar API in v1.

### 3.4 Integration seam in `main.py`

In `_do_dictation`, **after** the Phase 13 Command Mode block (ends ~line 744)
and **before** the scratchpad/inject block (~line 746), add a parallel block:

```python
if exp_cfg.get("action_mode"):
    from . import voice_actions as _va
    prefix = exp_cfg.get("command_prefix", "computer")
    body = _va.strip_prefix(cleaned, prefix)   # reuse commands.strip_prefix logic
    if body is not None:
        match = _va.classify(body, self.cfg)
        if match is not None:
            ctx = _va.ActionContext(
                focused_title=title,
                focused_path=self.injector.focused_document_path(),  # NEW, see 3.5
                cfg=self.cfg, notify=wnotify.notify,
                cleaner=self.cleaner, history=self.history,
            )
            ok, msg = _va.dispatch(match, ctx)
            wnotify.notify("Echo Flow", msg, "info" if ok else "warning")
            if self.history is not None:
                self.history.log_action(
                    body=body, handler=match.name, args_json=json.dumps(match.args),
                    label=match.label, ok=ok, error=None if ok else msg,
                )
            if self.tray:
                self.tray.set_state("ok" if not self._paused else "paused")
            return   # actions never leave a paste behind
        # else: fall through. Do NOT notify "unknown" here if command_mode
        # might still want it — but since action_mode is its own prefix space,
        # an unknown action SHOULD notify, matching Command Mode's UX.
```

**Ordering decision:** Command Mode and Action Mode share the `"computer"`
prefix. Run **Command Mode first** (keystrokes are higher-precision, lower-risk),
and only fall through to Action Mode if Command Mode returns no match. Implement
this by having the Command Mode block, on no-match, **not** notify/return but
instead set a local `cmd_unmatched = True` and let execution reach the Action
Mode block. Only notify "unknown command" if *both* miss. Refactor carefully and
keep Command Mode's existing behavior identical when `action_mode` is off.

### 3.5 New injector helper: `focused_document_path()`

Add to `src/inject.py` a best-effort `focused_document_path() -> str | None`
that, on Windows, tries to resolve the file path of the foreground window's
document (e.g. via the window title + known app heuristics, or UI Automation if
already available). Return `None` when it can't — handlers must tolerate None.
**Do not block the hot path**; this only runs when an action actually fires.

### 3.6 Config additions (`config.yaml`, in the `experimental:` block)

```yaml
experimental:
  press_enter_command: false
  command_mode: false
  command_prefix: "computer"
  action_mode: false              # Phase 14 — semantic voice actions
  action_apps:                    # name (as spoken) -> launch target
    spotify: "spotify"            # resolved via shutil.which / Start Menu
    notepad: "notepad.exe"
    browser: "https://www.google.com"   # a URL target is allowed here too
  action_email_url: "https://mail.google.com"
  action_summary_max_chars: 6000  # cap text sent to local summarizer
```

---

## 4. Safety checklist (must all be true before merge)

- [ ] `action_mode` defaults to `false`; with it off, behavior is byte-identical
      to today (prove with a test that runs `_do_dictation` flag-off).
- [ ] No code path passes spoken text into `shell=True` or a raw command string.
- [ ] `open_app` only launches keys present in `action_apps`.
- [ ] `open_url` rejects non-http(s)/mailto schemes and metacharacters.
- [ ] No handler deletes/sends/pays. `draft_event` writes a local `.ics` only.
- [ ] Every action attempt is logged to `voice_actions` (ok and fail).
- [ ] Every handler catches its own exceptions and returns `(False, msg)`.
- [ ] Unknown action under the prefix notifies the user (no silent paste).
- [ ] Plain dictation (no prefix) can never trigger an action — test it.

---

## 5. Tests (add to `tests/`)

Follow the existing test style in `tests/`. At minimum:

1. `test_voice_actions_classify.py`
   - prefix stripping parity with `commands.strip_prefix`
   - "open spotify" → `open_app` with `args={"app":"spotify"}` (when configured)
   - "open spotify" with spotify NOT in `action_apps` → match but dispatch fails
     cleanly
   - "search google for cats" → `web_search` with correct urlencoded query
   - "open email" → `open_url` to the configured mail URL
   - non-prefixed text → `classify` never called / returns None
   - injection attempt: "open spotify && rm -rf /" → app key lookup fails, no
     shell exec (assert `subprocess` not called with the raw string)
2. `test_voice_actions_dispatch.py`
   - mock `webbrowser.open` / `subprocess.Popen` / `os.startfile`; assert each
     handler calls the right primitive with sanitized args and returns
     `(True, msg)`
   - `summarize_focused` with `focused_path=None` → `(False, ...)`, no crash
3. `test_main_action_mode_off.py`
   - with `action_mode: false`, a "computer open spotify" dictation still pastes
     text (or is handled by Command Mode) exactly as before — Action Mode is inert

Run: `scripts/run_tests.bat` (or `python -m pytest tests/ -q`).

---

## 6. Dashboard (optional, second PR)

The dashboard has an "experimental panel" that calls `commands.list_supported()`.
Add an Action Mode section that calls `voice_actions.list_supported(cfg)` and an
editor for the `action_apps` map. Out of scope for the first PR — land the engine
+ tests first.

---

## 7. Stretch: LoRA intent classifier (do NOT build in v1)

Regex is the right v1 — it's debuggable and zero-latency. Once you have ~100+
logged `voice_actions` rows, you can train a tiny intent classifier (you already
have `scripts/train_lora.py`) to handle phrasings the regex misses, with regex
as the high-precision fast path and the model as fallback. Keep this behind a
separate flag and out of the first PR. Note it in CHANGELOG as "future".

---

## 8. Deliverables for the first PR (safe trio)

1. `src/voice_actions.py` (engine: strip_prefix, classify, dispatch,
   list_supported, handlers for `open_app`, `open_url`, `web_search` **only**).
2. `log_action()` + `voice_actions` table migration in `src/history.py`.
3. Integration block + Command/Action shared-prefix fallthrough refactor in
   `src/main.py` (§3.4).
4. Config additions in `config.yaml` (`action_mode`, `action_apps`,
   `action_email_url`; `action_summary_max_chars` can wait for PR 2).
5. Tests in `tests/` (sections 5.1–5.3, minus the `summarize_focused` cases).
6. CHANGELOG.md entry + a short note in README.md "Experimental" section.

**Deferred to PR 2:** `focused_document_path()` (§3.5), `summarize_focused`,
`draft_event`, `quick_note`, and the dashboard panel (§6).

**Keep the diff surgical.** Don't refactor unrelated code. Match the file's
existing comment density and idiom. When done, run the test suite and report
results honestly.
