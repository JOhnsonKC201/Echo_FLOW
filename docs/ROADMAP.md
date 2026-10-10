# Echo Flow Roadmap

The single backlog for Echo Flow. Earlier lists (the Action Layer P2 items,
the launch-campaign seed issues, the audit "Deferred" list) are folded in
here so there is one place to look. Items are ideas until a PR lands; the
living record of what shipped is [`../CHANGELOG.md`](../CHANGELOG.md).

Ground rule, from `.github/ISSUE_TEMPLATE/feature_request.yml`: anything
that sends audio or text off the machine by default is declined. Opt-in
cloud paths are fine when the privacy ledger shows them.

## Where this list came from

On 2026-10-10 we audited Wispr Flow, the leading commercial dictation app,
against the Echo Flow code. Sources: wisprflow.ai (home, pricing, what's
new, why-flow, workflows, vibe-coding, accessibility, privacy) and the help
center at docs.wisprflow.ai (Using Wispr Flow collection, Command Mode,
shortcuts, Styles, Smart Formatting and Backtrack, Auto Cleanup, Transforms,
Snippets, Dictionary, IDE and terminal articles). Every idea below was
checked against `src/` first; items Echo Flow already has are listed at the
end so nobody re-proposes them.

Priorities: P0 next up, P1 after that, P2 when someone wants it.

## Adopt

Missing today, fits local-first, and users of any dictation tool expect it.

### P0

| Idea | Why | Where it lands |
|---|---|---|
| **Spoken formatting commands**: "new line", "new paragraph", "comma", "full stop", "question mark", spoken list numbering ("one... two..." or "first... second...") | Wispr honors these regardless of cleanup level. Echo Flow has none, so a user who wants a paragraph break has to type it. | Deterministic pre-cleanup pass, new `src/spoken_format.py`, called from `src/cleanup.py` before the model. Must also run when `cleanup.provider: none`. Config `cleanup.spoken_punctuation` (default on). Never emits an em dash. |
| **Backtrack / self-correction**: "5 pm, actually 6" becomes "6 pm"; "scratch that", "never mind", or just restating | Wispr's headline differentiator over built-in dictation. Echo Flow's prompts collapse some false starts but there is no rule, no test, and no deterministic path. | Explicit rule in the cleanup system prompts in `src/cleanup.py`, a deterministic "scratch that" cut in the same pre-pass, and grader cases in `src/grade.py`. Only clear corrections are removed ("I actually enjoyed it" stays). |
| **Undo AI edit**: get the raw Whisper text of a past dictation back | `dictations.raw_text` is already stored in `src/history.py`. Nothing exposes it. | Tray item "Paste raw of last dictation", a per-row button on the `/` inbox, and a `hotkey.paste_raw_combo`. |
| **Auto Cleanup level** as one top-level dial: None / Light / Medium | Wispr collapsed its per-feature toggles into three cards and removed "High" as too aggressive. Echo Flow has seven styles plus per-app profiles, which is more power than a new user wants on day one. | Map onto what exists: None = `cleanup.enabled: false`, Light = `default`, Medium = `medium`. Three cards at the top of `/style` (`src/dashboard/style_profiles.py`, `templates/style.html`); per-app profiles stay below as the advanced view. |

### P1

| Idea | Why | Where it lands |
|---|---|---|
| **Hands-free as its own binding**, plus double-tap push-to-talk to lock | `hotkey.mode: toggle` is global. Wispr lets hold and toggle coexist (Ctrl+Win vs Ctrl+Win+Space on Windows). | `hotkey.handsfree_combo` in `src/hotkey.py`; spec validation in `src/hotkey_spec.py`. |
| **Cancel key (Esc) while recording** and inline retry of a failed dictation | Today the only abort is pressing Win inside the Ctrl+Shift frame. | `src/hotkey.py` + `src/main.py`; a "Retry" toast action via `src/notify.py`. |
| **Copy last transcript** hotkey, next to paste-last | Recovery path when paste fails (WSL, SSH, remote desktop all swallow direct paste). | `hotkey.copy_last_combo`, `src/inject.py`. |
| **Mouse-button bindings** (middle, Mouse 4/5) for push-to-talk or Enter | Wispr "Mouse Flow". Many users have a spare thumb button. | pynput mouse listener in `src/hotkey.py`; `src/hotkey_spec.py` accepts `mouse4`/`mouse5`/`middle` tokens. |
| **Dictionary "correct a misspelling" pairs**: wrong spelling to right spelling, whole word, keeps saved capitalization | The pattern miner in `src/learn.py` learns some of this implicitly, but a user cannot type the pair in directly. | New entry type in `src/dashboard/vocabulary.py` and `/dictionary`; applied after cleanup like snippets. |
| **Auto-apply a Transform after dictation** | Transforms run only as a one-shot hotkey. Wispr runs the chosen transform between transcription and paste, and pastes the original if it fails. | `auto_apply` field in `src/dashboard/transforms.py`; skip when the verify score is low; original pasted on error. |
| **Terminal-aware paste** | Wispr uses Shift+Insert in editors and terminals on Windows, pastes long text into Claude Code and Codex CLIs in chunks, and delays clipboard restore per app (3 s for Outlook/Word/WhatsApp, 5 s for remote desktop). Overlaps the "clipboard race on rapid dictation" item deferred in `AUDIT_2026-06-03.md`. | `src/inject.py`, keyed off the window-title profiles. |

### P2

| Idea | Why | Where it lands |
|---|---|---|
| **Audio playback in history**, opt-in, auto-purged after N days | Lets a user hear what they said when cleanup got it wrong. `history.keep_audio` exists but is false and audio never touches disk. | `history.keep_audio_days` with a purge job; the `/privacy` ledger must show it. |
| **Mic ranking, lid-closed switch, virtual mics** | `audio.fallback_mics` already covers the dead-mic case (2026-10-08). Wispr adds a ranked list UI, switches to the best external mic when a laptop lid closes, and lists virtual devices (Krisp, NVIDIA Broadcast) behind "Show other devices". | Settings > system; `src/audio.py`; lid state on macOS. |
| **Language nudge** | When Whisper detects a language other than the configured one, offer to switch. | Whisper language probability already comes back from `src/transcribe.py`; toast via `src/notify.py`. |
| **"?" hotkey overlay** and keyboard / screen-reader pass on the dashboard | Was seed issue 5 in the launch campaign. Wispr ships screen-reader support and markets to RSI and Parkinson's users; Echo Flow's local-first story is a stronger accessibility pitch if the dashboard is navigable. | `templates/*.html`, sourced from config so custom combos show. |
| **Snippets with rich text** | Bold, lists, links preserved where the target app accepts HTML, plain fallback elsewhere. | HTML clipboard payload in `src/inject.py`; editor on `/snippets`. |
| **Floating scratchpad window** on a hotkey | Scratchpads exist as dashboard pages. Wispr's is a small always-on-top notepad. | PyWebView mini-window in `src/dashboard/window.py`. |
| **Insights "Your Voice" card**, shareable PNG | `/insights` has the numbers already. | `src/dashboard/analytics.py` + a render route. |
| **Idle-time updates, quiet start at login** | `src/update_check.py` only notifies today. | Low priority. |
| **Window commands** (`new_window`, `switch_window`, `minimize`) via a fixed safe hotkey allowlist | CAT-WINDOW from the Action Layer roadmap. | `src/voice_actions.py`, `_SAFE_WINDOW_COMBOS`. |
| **Scoop manifest** | Seed issue 1. | `packaging/scoop/echoflow.json`. |
| **Calibration sentence packs** for the other 15 languages | Seed issue 2. | `src/calibration.py` sentence source. |
| **`docs/MODELS.md`** table of tested Ollama models | Seed issue 3. | docs. |
| **Starter snippet packs** (dev, medical, legal, email) | Seed issue 4. | Importable from `/snippets`. |

## Adapt

Wispr does these in the cloud. Echo Flow can do a local version.

- **Context awareness (names, identifiers, file tagging).** Wispr reads a
  slice of on-screen text to spell names and jargon, matches "user ID" to
  `userId` from visible code in Cursor/VS Code, and turns a spoken filename
  into an @-mention in Cursor's chat. Local variant: read the focused
  window's accessible text through UI Automation (Windows) or AX (macOS),
  feed the top identifiers into Whisper's `initial_prompt` alongside the
  dictionary (the 80-term cap in `src/main.py` applies). Off by default,
  listed on `/privacy` even though nothing leaves the machine, secure
  fields always excluded. Start with filename @-tagging for Cursor and VS
  Code chat panels.
- **Comparison pages.** Wispr has "Flow vs Apple Dictation" and "Flow vs
  Gboard". Echo Flow's natural pages are "vs Windows Voice Typing" and
  "vs Wispr Flow" (local, free, no word cap), on the landing page.
- **Workflow gallery and audience pages** (developers, students, lawyers,
  accessibility). Landing-page content, community-submitted.
- **Known issues page.** Wispr publishes post-mortems and a status page.
  The local equivalent is a `docs/KNOWN_ISSUES.md` fed from the audit
  "Deferred" lists (clipboard race, `method: type` dropping non-ASCII, PID
  file path on frozen installs, SQLite `pow()` fallback, flash-message URL
  encoding across dashboard routes, `config_writer` `.tmp` leak).

## Not planned

Recorded so they are not re-proposed.

| Idea | Reason |
|---|---|
| Cloud transcription, cloud sync of history | Local-first is the product. The iOS keyboard's optional bridge is the one sanctioned remote path and it stays on the LAN. |
| Team shared dictionary and snippets, admin portal, SSO, SCIM, audit logs, HIPAA BAA, usage leaderboard | Needs an account server. Out of scope for a single-user local tool. |
| Notetaker (recording other people's meetings) | Consent and scope. If ever, a separate tool. |
| "Ask Perplexity" style search command | Action Mode already has web search with the user's own engine. |
| Spoken "send", "post", "submit", deletion, shell timers, arbitrary-path open | CAT-REJECT in `ACTION_LAYER_ROADMAP.md`. "submit" and "send it" were tried and removed for false positives. |
| Shipping per-app styles differentiated by default | Tried. On 2026-07-26 every profile was set to `medium` on purpose because `polished` rewrote too hard and `default` was too light (see the comment above `cleanup.profiles` in `config.yaml`). The README should describe profiles as available, not as the default. |

## Doc drift found during the audit

Claims that no longer match the code. Fix in a docs PR.

- `docs/index.html`, `PRODUCT_OVERVIEW.md`, `.claude/campaigns/echo-flow-launch/positioning.md`:
  "raw Whisper text without Ollama". The code now does rules-only cleanup
  (fillers, casing, punctuation) via `src/fillers.py`. README is correct.
- `docs/index.html`, `positioning.md`: "Windows 10/11 only". macOS runs from
  source (no installer yet).
- `README.md` badge "audio 100% on-device" and the iOS blurb, `ios/README.md`:
  the iOS keyboard defaults to Groq cloud transcription and polish
  (`ios/EchoFlowCore/SharedConfig.swift`). Either flip the default to
  bridge-first or scope the badge to the desktop.
- `README.md` features: "casual punctuation in Slack, full sentences in
  Gmail" describes profiles that ship as all `medium`. Soften to "can".
- `config.yaml` and `packaging/default/config.yaml`: the comment above
  `experimental:` says toggling "has no runtime effect yet". The flags are
  wired in `src/main.py`.
- `PRODUCT_OVERVIEW.md`: version 0.3.0 and "1502 tests" are stale.
- `src/dashboard/graph_obsidian.py` loads D3 from d3js.org, the one dashboard
  page that reaches the public internet. Vendor it or say so on `/privacy`.

## Already there (do not re-propose)

Checked against Wispr Flow's feature list on 2026-10-10. Echo Flow already
has: local Whisper (CUDA, CPU, Apple silicon) with no cloud speech-to-text;
filler removal, casing and punctuation with a rules-only fallback when no
model is up; a quality grader with a second pass; seven cleanup styles and
per-app window-title profiles; a dictionary that biases Whisper's
`initial_prompt` and suggests low-confidence words; snippets; Transforms on
hotkeys; one-shot Prompt-Engineering mode; My Voice writing samples; the
paste-in Humanizer; Command Mode and Action Mode with an intent fallback;
re-paste last dictation; fallback microphones; edit-to-train and voice
calibration; 16 languages plus auto-detect; scratchpads, notes, knowledge
graph and semantic search; the Outcomes page (time saved, wpm, latency,
streaks); the privacy ledger with export and wipe; Windows installer and
winget; macOS from source; an iOS keyboard prototype over a LAN bridge.
