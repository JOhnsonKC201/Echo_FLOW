# Action Layer Roadmap (Phase 14+)

Synthesis of 6 specialist facet reports against the real code:
`src/voice_actions.py`, the `src/main._do_dictation` seam (lines 700-789),
`src/history.py` (`voice_actions` table + `log_action`/`recent_actions`/`add_note`),
and `config.yaml experimental:`. Spec of record: `ACTION_LAYER_SPEC.md`.

---

## 0. Status (updated 2026-07-08)

Most of this roadmap has shipped. Treat sections 1–3 as the *original* design
record, not current state. Landed since:

- **PR 2** handlers (`summarize_focused`, `draft_event`, `quick_note`) + the
  `focused_document_path()` injector, plus SEC-1/2/4/5/7/8 hardening.
- **PR 3** dashboard `/actions` panel (`src/dashboard/actions_view.py`) + SEC-3
  arg redaction (`redact_args`) + `action_log_verbose`.
- **PR 4** catalog: `media_key`, `volume`, `open_folder`, `open_clipboard_link`
  (+ the `injector` field on `ActionContext`).
- **PR 5 / DASH-2** as **Option B**: SQLite-backed `action_targets` merged over
  config defaults (`user_targets()` + `history.set_action_target` etc.).
- Prefix-free triggering (`resolves()`) beyond the original scope.
- **PR 6 spine (this change):** `src/intent_model.py` — `build_match`
  (MODEL-BUILD), `classify_with_model`/`infer` with a confidence floor + length
  pre-gate (MODEL-WRAP), the `action_intent_model` flag incl. `shadow` value +
  lazy import wiring in `main._do_dictation` (MODEL-FLAG), a dependency-free
  `KeywordPredictor`, and `scripts/eval_intent.py` (offline precision/recall +
  a CI `--check` gate — the MODEL-SHADOW measurement need, met offline).

**Still open (future):** the real ML head — MODEL-HEAD (embedding + logistic
regression reusing the repo embedder), MODEL-DATA (training set mined from
`voice_actions`), MODEL-LATENCY tuning, and persisted MODEL-SHADOW rows
(a `model_pred` column) if online agreement measurement is wanted on top of the
offline harness. All of these plug into the existing spine via `set_predictor()`
without touching a single guard.

---

## 1. Executive Summary

PR 1 shipped a genuinely conservative safe trio (`open_url`, `web_search`,
`open_app`): prefix-gated, off by default, allowlist-only apps, list-form
`subprocess` with `shell=False`, http/https/mailto-only URLs, and handlers that
catch internally and return `(ok, msg)` instead of raising. The architecture is
sound and there is no obvious path from voice to an arbitrary shell.

The work ahead splits cleanly into four streams:

1. **Security hardening** of the already-shipped trio — the safety *model* is
   right but the URL validator (`_is_safe_url`) and the `open_app` allowlist
   lookup have real adversarial gaps (percent-encoding, IDN homographs,
   userinfo spoofing, mailto query injection, a launch-failure path that
   *broadens* execution semantics). These are small, targeted fixes and must
   land before the surface grows.
2. **PR 2 feature handlers** — `summarize_focused`, `draft_event`, `quick_note`,
   plus the `focused_document_path()` injector helper. The scaffolding
   (`ActionContext`, `_HANDLERS`, `log_action`, `add_note`) already exists;
   PR 2 is purely additive and local-first.
3. **Dashboard `/actions` panel** — the data layer is done (`list_supported`,
   `recent_actions`); the read-only views are a near-mechanical clone of the
   Commands section.
4. **New action catalog + optional intent model** — media/volume/window
   controls (zero new deps, reuse the injector), and a regex-miss tiny local
   intent classifier that *never* fires a side effect directly (re-validated
   through the same guards). Both are later, flagged, and out of PR 2.

The single most important invariant to preserve everywhere: **the allowlist and
URL-scheme checks remain the sole authority on what executes, regardless of who
proposed the action** (regex, model, or replayed log row).

### Top 3 P0 items (do these first)

- **SEC-1** — Harden `_is_safe_url`: reject userinfo, control/percent-encoded
  metacharacters (decode before checking), and non-ASCII (IDN homograph) hosts.
- **SEC-2** — Restrict `mailto:` to a bare address; reject `?subject=/?body=/?attach=/cc/bcc`
  header-injection query parameters.
- **PR2-CFG** — Add the three PR-2 regexes + handlers in the correct `classify()`
  order, add the missing `experimental.action_summary_max_chars` config key,
  surface in `list_supported()`, and extend the classify/dispatch test suites.

---

## 2. Prioritized Backlog (all facets, de-duplicated)

Effort: S/M/L. Risk: low/med. IDs are stable handles for sequencing.

### P0 — do first (safety + PR-2 foundation + highest-value tests)

| ID | Item | Facet | Effort | Risk |
|----|------|-------|--------|------|
| SEC-1 | Harden `_is_safe_url`: reject userinfo, decode before metachar check, enforce ASCII host (anti-homograph) | Security | M | low |
| SEC-2 | Restrict `mailto:` to bare address (no `subject/body/cc/bcc/attach` query) | Security | S | low |
| PR2-CFG | New regexes in correct `classify()` order + `action_summary_max_chars` config + `list_supported()` + classify/dispatch tests | PR-2 | M | low |
| MODEL-BUILD | `build_match(handler, slot, cfg)` re-validator so any future model output flows through the SAME guards | Intent model | S | low |
| MODEL-WRAP | `classify_with_model()` confidence floor + re-run through `build_match` (model can only resolve to already-safe ActionMatches) | Intent model | M | low |
| MODEL-FLAG | Separate `action_intent_model` flag (default false), lazy-loaded, consulted only on regex miss | Intent model | M | low |
| DASH-1 | `/actions` read-only section: `actions_view.page_data` + `actions.html` + GET route (mirror Commands) | Dashboard | S | low |
| TEST-FALLTHROUGH | Test Command->Action prefix collision/fallthrough in `_do_dictation` ("go to the top" = Ctrl+Home, "go to github.com" = open_url) | Tests | M | low |
| TEST-BROWSEROPEN | Test `webbrowser.open()==False` branch in all three handlers | Tests | S | low |
| TEST-DOMAIN | Test `_domain_to_url` branches: explicit scheme, mailto, ports/paths/queries, "never guess TLD" | Tests | M | low |

### P1 — next (PR-2 handlers, catalog, hardening, key tests)

| ID | Item | Facet | Effort | Risk |
|----|------|-------|--------|------|
| PR2-FOCUS | `focused_document_path()` Win32 injector helper + wire `focused_path=` in main.py:753 | PR-2 | M | med |
| PR2-SUMMARIZE | `summarize_focused` handler — reuse `ctx.cleaner` pinned to ollama, lazy pymupdf/docx, graceful degrade | PR-2 | L | med |
| PR2-EVENT | `draft_event` handler — local `.ics` to `data/drafts/` + `os.startfile`, lazy dateparser | PR-2 | L | low |
| PR2-NOTE | `quick_note` handler — reuse `history.add_note` + `notes._auto_title` | PR-2 | S | low |
| SEC-3 | Redact/minimize sensitive args before logging to `voice_actions` (PII in queries/URLs) | Security | M | low |
| SEC-4 | Fix `open_app` allowlist lookup: single-pass case-fold, collision warning, validate target shape | Security | M | med |
| SEC-5 | Tighten `FileNotFoundError -> os.startfile` fallback to alias-shaped/existing-path tokens only | Security | S | low |
| CAT-MEDIA | Media transport keys (play/pause, next, prev) via injector; needs `injector` field on ActionContext | Catalog | M | low |
| CAT-VOLUME | Volume up/down/mute via injector, hardcoded step count (no spoken numbers) | Catalog | M | low |
| CAT-FOLDER | `open_folder` via `action_folders` allowlist map + `os.startfile`, `isdir` guard | Catalog | M | low |
| DASH-2 | `action_apps` editor (mapping-aware writer OR SQLite-backed store — config_writer is scalar-only) | Dashboard | M | med |
| DASH-3 | Surface `action_mode` toggle + `action_email_url` in settings/experimental, link from /actions | Dashboard | S | low |
| MODEL-DATA | Training-data extraction from `voice_actions` (ok=1 trusted; regex-self-label ok=0; mine "none" class) | Intent model | M | med |
| MODEL-HEAD | Tiny embedding + logistic-regression intent head (NOT a LoRA on 8B); reuse repo embedder | Intent model | M | med |
| MODEL-LATENCY | Load-once, length pre-gate, CPU embed cap, warm-load hook + timing log | Intent model | S | low |
| TEST-IDN | Test unicode/IDN/punycode domains in classify and `_is_safe_url` (pin chosen contract) | Tests | M | med |
| TEST-PREFIX | Test custom `command_prefix` end-to-end through classify+dispatch+main | Tests | S | low |
| TEST-EMPTYARGS | Test empty/None/whitespace args reaching handlers directly | Tests | S | low |
| TEST-FAILLOG | Test failure-row logging (ok=False persists error) and `log_action` best-effort on raise | Tests | M | low |

### P2 — later (polish, edge cases, deferred capability)

| ID | Item | Facet | Effort | Risk |
|----|------|-------|--------|------|
| SEC-6 | Clarify Command/Action fallthrough so `cmd_body`/unknown-toast can't mislabel or double-notify | Security | S | low |
| SEC-7 | Anchor/de-greedy `_RE_DOMAIN` (ASCII TLD 2-24, no confusables, no "node.js"->nav) | Security | M | med |
| SEC-8 | Bound `webbrowser.open` (cap query length to 512, `new=2, autoraise=True`, log host only) | Security | S | low |
| CAT-CLIP | `open_clipboard_link` — open clipboard contents only if it passes `_is_safe_url` | Catalog | S | low |
| CAT-WINDOW | `new_window`/`switch_window`/`minimize` via fixed safe hotkey allowlist | Catalog | M | med |
| CAT-REJECT | Documented rejected ideas (shell timers, send/post, deletion, arbitrary-path open) — do NOT build | Catalog | S | low |
| MODEL-SHADOW | Shadow-mode logging: record model predictions without executing to measure precision vs regex | Intent model | L | med |
| TEST-PUNCT | Test trailing-punctuation normalization + `_RE_SEARCH` empty-query edge cases | Tests | S | low |
| TEST-STARTFILE | Test `os.startfile` FileNotFoundError fallback in `_launch_executable` | Tests | M | low |
| TEST-NORAISE | Test dispatch never raises on malformed ActionMatch + `_is_safe_url` edge inputs | Tests | S | low |

---

## 3. Per-Facet Detail (best concrete sketches preserved)

### 3.1 Security & Abuse Hardening

**SEC-1 — harden `_is_safe_url` (voice_actions.py:162-169).** The current check
`any(c in _URL_FORBIDDEN for c in url)` does not decode percent-encoding, accepts
userinfo, and lets unicode-confusable hosts through (`\w` is unicode-aware).

```python
def _is_safe_url(url: str) -> bool:
    if not url or any(c in _URL_FORBIDDEN for c in url):
        return False
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https", "mailto"):
        return False
    # decode then re-check for control / metachar smuggling (%0a %3b %00 ...)
    decoded = urllib.parse.unquote(url)
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in decoded):
        return False
    if any(c in _URL_FORBIDDEN for c in decoded):
        return False
    if parsed.scheme in ("http", "https"):
        host = parsed.hostname or ""
        if not host or parsed.username is not None or parsed.password is not None:
            return False  # block userinfo spoofing (google.com@evil.com)
        try:
            host.encode("ascii")     # defeat IDN homographs; or idna-encode+compare
        except UnicodeEncodeError:
            return False
    return True
```
Tests: `https://a:b@evil.com`, `http://exа­mple.com`, `https://x/%0aFoo`, `HTTP://x.com`.

**SEC-2 — restrict mailto.** Special-case inside `_is_safe_url`:
```python
    if parsed.scheme == "mailto":
        return parsed.query == "" and "?" not in url and "@" in parsed.path
```
Permits `mailto:me@x.com`, rejects `?body=`/`?attach=`/`?cc=`. Tests:
`mailto:a@b.com?body=secret`, `mailto:a@b.com?attach=/etc/passwd`, `mailto:a@b.com`.

**SEC-3 — redact args before logging (main.py:762-767 + new helper).**
```python
def redact_args(name, args):
    a = dict(args or {})
    if name == "web_search" and "query" in a:
        a["query"] = "<redacted len=%d>" % len(a["query"])
    if name == "open_url" and "url" in a:
        s = urllib.parse.urlsplit(a["url"])
        a["url"] = f"{s.scheme}://{s.hostname or ''}"   # host only
    return a
```
Call site: `args_json=json.dumps(_va.redact_args(match.name, match.args))`. Gate
full-body logging behind an explicit `experimental.action_log_verbose` (default false).

**SEC-4 — open_app lookup (voice_actions.py:203-225).** Normalize once, detect
collisions, validate target shape before `_launch_executable`:
```python
norm = {str(k).strip().lower(): str(v).strip() for k, v in apps.items()}
# warn if len(norm) < number of distinct stripped-lower keys (silent collision)
target = norm.get(app)
```
Reject targets containing whitespace+args unless `os.path.isfile(target)`; reject
embedded `/c`, `&`, `|`, `&&`.

**SEC-5 — tighten startfile fallback (voice_actions.py:241-250).** Only call
`os.startfile` when the target is alias-shaped or an existing file:
```python
alias_ok = bool(re.fullmatch(r"[\w.+-]+(\.exe)?", target))
path_ok = os.path.isfile(target)
if sys.platform == "win32" and (alias_ok or path_ok):
    os.startfile(target)
else:
    return (False, f"Couldn't find {app} on this system.")
```
Preserves the `spotify` App-Execution-Alias case; refuses `cmd /c calc`.

**SEC-6 — fallthrough clarity (main.py:711-789).** Compute the prefix-stripped
body once; assert the invariant that reaching line 777 implies a genuine
double-miss. Regression test: both modes on + unmatched prefixed utterance =
exactly one warning toast and one `unknown` log row.

**SEC-7 — anchor `_RE_DOMAIN` (line 85).** Require an ASCII final TLD label so a
spoken phrase with a dot can't be silently navigated:
`^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}(/\S*)?$` with `re.ASCII`. Tests:
`node.js`, `my.report`, `exа­mple.com`, `github.com`, `docs.python.org/3/`.

**SEC-8 — bound `webbrowser.open`.** Cap `query = query[:512]` in `_h_web_search`;
prefer `webbrowser.open(url, new=2, autoraise=True)`; log host only.

### 3.2 PR-2 Deferred Handlers

**PR2-FOCUS — `focused_document_path()` (inject.py + main.py:753).** Best-effort,
never raises, returns None when unresolved (every handler tolerates None). Mirror
the existing `_focused_window_title()` win32gui pattern: take the foreground
window title, regex a `foo.(pdf|txt|md|docx)` token, and resolve against
`cwd, ~/Documents, ~/Desktop, ~/Downloads`. Wire `focused_path=self.injector.focused_document_path()`.

**PR2-SUMMARIZE — reuse the Cleaner, never a cloud call.** No args from voice
(target is `ctx.focused_path` only). Ext allowlist `{.pdf,.txt,.md,.docx}`; cap
text at `action_summary_max_chars` (default 6000). Critical call:
```python
summary, _ = cleaner.clean(text, system_prompt_override=sys_prompt,
                           provider_override="ollama")  # pins local + swaps prompt
```
`clean()` uses `provider = provider_override or self.provider`, so passing
`'ollama'` pins local; the local-only enforcement (cleanup.py ~497-503) is defense
in depth. Lazy-import `fitz`/`docx`; missing dep -> empty text -> friendly degrade.
Persist the full summary via `history.add_note(dictation_id=None, ...)` and return
a truncated string for the toast.

**PR2-EVENT — local .ics draft only, never a calendar API.** Regex
`^(?:create|add|make|schedule)\s+(?:a\s+)?(?:calendar\s+)?(?:event|meeting|appointment)\s+(?:for\s+|about\s+|called\s+|titled\s+)?(.+)$`.
Write to `Path("data")/"drafts"` (relative — frozen builds chdir to USER_ROOT,
so do NOT hardcode an absolute root), sanitize filename via `[^\w.-]+ -> _`
(path-traversal guard), then best-effort `os.startfile` on win32. Lazy
`dateparser`; absent/unparseable -> deterministic "tomorrow 9am" + honest message.

**PR2-NOTE — reuse the existing notes store.** Regex
`^(?:take|make|add|create|write)\s+(?:a\s+|me\s+a\s+)?note(?:\s+(?:that|saying|about))?[:\s]+(.+)$`.
`title = notes._auto_title(body)`; `history.add_note(dictation_id=None, title=title, description=body)`.
No network, no filesystem, no new deps, no schema change (notes table already migrated).

**PR2-CFG — ordering + config + surfacing.** `classify()` order: EMAIL -> SEARCH
-> GOTO -> SUMMARIZE -> EVENT -> NOTE -> OPEN (OPEN stays the catch-all; the three
new verbs are non-"open" so order vs `_RE_OPEN` is safe). Add to `config.yaml`:
```yaml
  action_summary_max_chars: 6000  # cap text sent to the local summarizer
```
Append three labels to `list_supported()`. Extend
`tests/test_voice_actions_classify.py` and `tests/test_voice_actions_dispatch.py`.
Recommend adding `pymupdf>=1.24` (and optionally `python-docx`, `dateparser`) to
requirements.txt since the spec asks to confirm pymupdf is present; handlers must
still degrade gracefully if absent.

### 3.3 New Action Catalog

**Shared prerequisite for keystroke handlers:** add `injector: object | None = None`
to `ActionContext` (~line 60) and thread `injector=self.injector` at the dispatch
site (main.py:751). open-style handlers (folder, clipboard) need no such change.

- **CAT-MEDIA** — `_RE_MEDIA`/`_RE_NEXT`/`_RE_PREV` -> `media_key` with hardcoded
  `playpause`/`nexttrack`/`prevtrack`; `inj.send_key(key)`. Word "go back a track"
  carefully to avoid colliding with Command Mode's `^go\s+back\b`.
- **CAT-VOLUME** — `volumeup`/`volumedown`/`volumemute`; step count hardcoded
  (clamp `max(1, min(n, 5))`), never parsed from spoken text.
- **CAT-FOLDER** — new `experimental.action_folders` map (mirrors `action_apps`);
  spoken name is a lookup key, resolved value `expandvars/expanduser`, guarded by
  `os.path.isdir` then `os.startfile`. Voice can never open an arbitrary path.
- **CAT-CLIP** — `open_clipboard_link` reads clipboard, runs it through
  `_domain_to_url`/`_is_safe_url`, then reuses `_h_open_url`. No args logged.
- **CAT-WINDOW** — fixed `_SAFE_WINDOW_COMBOS = {"ctrl+n","alt+tab","win+down","win+up"}`
  via `inj.send_hotkey`; explicit allowlist mirrors `commands.SAFE_*` discipline.
- **CAT-REJECT (do NOT build):** shell-based timers (`timeout`/`schtasks`/`Start-Sleep`),
  any send/email/post handler, recycle-bin/delete/close-without-save, arbitrary
  spoken-path open, and `run <command>`. All violate locked constraints.

### 3.4 Dashboard `/actions` Panel

- **DASH-1** — `src/dashboard/actions_view.py` mirroring `commands_view.py`:
  `page_data(cfg, history)` returns `enabled/prefix/email_url/apps/supported/recent`
  using `voice_actions.list_supported(cfg)` and `history.recent_actions(limit=30)`.
  Add `('actions','Actions','/actions','actions.html')` to `SECTIONS` (app.py:52),
  a GET route mirroring `commands_page` (app.py:728), nav label in base.html, and
  `templates/actions.html` cloned from `commands.html` (recent table adds an
  `error` column shown muted when `not ok`). Zero new backend.
- **DASH-2** — `action_apps` editor. `config_writer.set_scalar` refuses
  mappings (config_writer.py:151-156). Decide: **Option A** add `set_mapping()`
  to the comment-preserving YAML writer (keeps config.yaml the source of truth);
  **Option B** SQLite-backed `action_apps` table merged over the config default
  (honors the "collections live in SQLite" architecture, mirrors snippets/transforms).
  Either way, server-side validation must enforce locked constraints: targets are
  app names or `_is_safe_url`-passing http/https/mailto only, never composed into
  a shell command. This touches a safety-relevant allowlist — own PR.
- **DASH-3** — `action_mode` (bool) and `action_email_url` (scalar) are both
  writable today via `config_writer.set_scalar`; add controls to
  settings/experimental and trigger the existing reload_config callback.

### 3.5 Tiny Local Intent Classifier (regex-miss fallback)

Locked invariant: the model **never fires a side effect the regex/allowlist
wouldn't.** It predicts a handler NAME + slot text only; the slot is re-parsed by
deterministic builders reusing `_domain_to_url`/`_is_safe_url`/the `action_apps`
lookup.

- **MODEL-BUILD** — `build_match(handler, slot, cfg) -> ActionMatch | None` reuses
  the SAME validators as `classify()`; for `open_app` it refuses non-allowlisted
  apps at construction time (stricter than classify, which defers to dispatch).
- **MODEL-WRAP** — `classify_with_model(body, cfg)`: abstain if `handler == "none"`
  or `conf < action_intent_min_conf`; extract slot via existing regex groups; pass
  through `build_match`. Any failure -> None -> exactly today's regex-only path.
- **MODEL-FLAG** — `experimental.action_intent_model: false` (+ `action_intent_model_path`,
  `action_intent_min_conf: 0.75`). In `_do_dictation`: `if match is None and
  exp_cfg.get("action_intent_model"): match = _va.classify_with_model(body, self.cfg)`.
  Lazy-import a new `src/intent_model.py` so ML deps never enter the hot import path.
- **MODEL-HEAD** — embedding + logistic-regression (~20MB, CPU, sub-10ms), NOT a
  LoRA on Llama-3.1-8B. Reuse the repo's existing embedder. `scripts/train_intent.py`
  clones the `train_lora.py` CLI scaffolding (`--check/--export/--train`, DB_PATH).
- **MODEL-DATA** — training set from `voice_actions`: `ok=1` rows are trusted
  positives; `ok=0` rows kept only if a regex still classifies the body; mine the
  essential `none` class from `command_log` `action_type='unknown'` rows.
- **MODEL-LATENCY** — pre-gate (`len(b) > 80 or len(b.split()) > 12 -> None`),
  load-once module cache, warm-load hook in the existing background warmup thread,
  timing log; revisit if median > ~25ms.
- **MODEL-SHADOW** — third flag value `action_intent_model: 'shadow'`; add a
  nullable `model_pred` column to `voice_actions` via the idempotent migration
  block (history.py ~117-132), log predictions without executing, and a
  `--report` agreement matrix to pick `min_conf` from data.

### 3.6 Test & Robustness Gaps

P0 tests: cross-engine fallthrough (TEST-FALLTHROUGH), `webbrowser.open==False`
(TEST-BROWSEROPEN), `_domain_to_url` branches incl. "never guess TLD"
(TEST-DOMAIN). P1: IDN/punycode contract (TEST-IDN — decide reject vs
idna-encode and pin it), custom prefix end-to-end (TEST-PREFIX), empty/None args
(TEST-EMPTYARGS), failure-row + best-effort logging (TEST-FAILLOG). P2:
trailing-punct/`_RE_SEARCH` edges (TEST-PUNCT), `os.startfile` fallback
(TEST-STARTFILE), dispatch-never-raises + `_is_safe_url` edge inputs
(TEST-NORAISE). Run: `python -m pytest tests/ -q`.

---

## 4. Recommended PR Sequencing

**PR 2 — "PR-2 handlers + the hardening they ride on" (the next PR).**
Bundle the security fixes that touch the same files PR-2 already edits, so the
attack surface never grows without its guard:
- SEC-1, SEC-2 (harden `_is_safe_url` + restrict mailto) — must precede any
  surface growth.
- SEC-5 (constrain the startfile fallback) — small, same file.
- PR2-FOCUS, PR2-SUMMARIZE, PR2-EVENT, PR2-NOTE (the three handlers + injector).
- PR2-CFG (classify ordering, `action_summary_max_chars`, `list_supported`, deps).
- Tests: TEST-FALLTHROUGH, TEST-BROWSEROPEN, TEST-DOMAIN, plus the new
  classify/dispatch cases for the three handlers; TEST-EMPTYARGS, TEST-FAILLOG.
- Add `pymupdf` (and optionally `python-docx`, `dateparser`) to requirements.txt.

**PR 3 — "Dashboard /actions (read-only) + privacy."**
- DASH-1 (read-only panel — zero new backend), DASH-3 (enable toggle + email URL).
- SEC-3 (arg redaction in logging) + `action_log_verbose` flag — ships alongside
  the panel that exposes those logs.
- SEC-6 (fallthrough clarity), SEC-7/SEC-8 (regex anchoring + open bounds).
- TEST-IDN, TEST-PREFIX, TEST-PUNCT, TEST-STARTFILE, TEST-NORAISE.

**PR 4 — "Action catalog (media/volume/folder/window/clipboard)."**
- ActionContext `injector` field + main.py wiring.
- CAT-MEDIA, CAT-VOLUME (P1), then CAT-FOLDER, CAT-CLIP, CAT-WINDOW.
- SEC-4 (open_app lookup hardening) fits here as the allowlist surface expands.
- Record CAT-REJECT boundary in code comments.

**PR 5 — "action_apps editor."** DASH-2 (mapping-aware writer OR SQLite store) —
isolated because it mutates a safety-relevant allowlist and makes one real
architecture decision.

**PR 6+ — "Optional intent model" (separate flag, off by default, spec §7).**
MODEL-BUILD + MODEL-WRAP + MODEL-FLAG first (the safety re-validation spine, no
ML deps), then MODEL-DATA + MODEL-HEAD + MODEL-LATENCY, then MODEL-SHADOW to
choose `min_conf` empirically before trusting the model to fire.

**Permanent non-goals:** shell-from-voice, send/email/post, anything destructive,
non-http/https/mailto URLs, arbitrary-path/app launch outside the allowlist.
