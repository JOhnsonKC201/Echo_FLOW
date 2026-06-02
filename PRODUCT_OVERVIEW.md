# Echo Flow — Product Overview

**Local-first voice dictation for Windows.** Hold a hotkey, talk, release — the
cleaned-up text lands wherever your cursor is. No subscription, no audio leaving
your machine, no account. Transcription, cleanup, and learning all run on your
own computer.

> The pitch in one line: *everything the commercial dictation apps charge a
> monthly fee for, running entirely on your hardware, where your voice never
> touches someone else's server.*

---

## The core loop

1. **Hold `Ctrl+Shift`** — recording starts.
2. **Talk** — audio is captured locally.
3. **Release** — Whisper transcribes on your CPU/GPU, a local LLM lightly polishes
   it (punctuation, capitalization, filler removal), and the result is pasted into
   the focused app.

A green microphone in the system tray means it's ready. First launch downloads the
Whisper model once; after that it works fully offline.

---

## What it does

### Dictation
| Feature | What it gives you |
|---|---|
| **Local transcription** | OpenAI Whisper running on-device (`tiny` → `large-v3-turbo`, or `auto` by hardware). Nothing uploaded. |
| **Local cleanup** | A small LLM via Ollama (`qwen2.5:3b-instruct`) polishes raw output. No Ollama → you still get raw Whisper text. |
| **Re-paste** (`Ctrl+Shift+Win`) | Drops your last dictation into a new window — say it once in Slack, paste it again in email. |
| **Snippets** | Short codes expand post-cleanup: "btw" → "by the way", "lgtm" → "looks good to me". Case- and word-boundary-aware. |
| **App-aware profiles** | Cleanup style adapts to the focused app — casual punctuation in Slack, symbol-aware in VS Code, full sentences in Gmail. |
| **Hallucination guard** | Length + RMS gate drops silent/short clips so Whisper can't invent "thank you for watching". |

### It learns your voice
Every correction you make through the tray menu feeds back into the cleanup model.
After a few hundred dictations it knows your jargon, names, and writing style.

| Capability | Detail |
|---|---|
| **Self-grading** | Every dictation gets a 0–100 quality score from four signals (Whisper confidence, hallucination guard, semantic coherence, pattern coverage). |
| **Self-improving loops** | Online weight calibration (SGD against your edits) + exponential pattern decay (14-day half-life) so stale jargon fades. |
| **LLM-free mode** | A `learned` cleanup provider built from your past corrections — runs with no LLM at all once it has enough signal. |
| **Auto-phasing** | Progresses from local Whisper + Ollama cleanup → fully self-sufficient LLM-free cleanup as your correction history grows. |

### Knowledge layer
| Feature | Detail |
|---|---|
| **Notes** | Pin any dictation to promote it to a long-lived knowledge object with title + description. |
| **Tags** | Three-signal auto-suggestion (cluster, similar, concept) with manual confirm. |
| **Action items** | Regex extraction of TODO-style phrases, with a blocklist for daily drivel. |
| **Knowledge graph** | D3.js force-directed view of your dictations/notes/concepts, with tag filters, search, and a quality slider. |
| **Review queue** | Worst-quality-first list of un-edited dictations, one click from the tray. |

---

## The desktop dashboard

A native local window (Flask + PyWebView, server-rendered, zero CDN/telemetry) for
managing everything: history, insights, custom vocabulary, snippets, style profiles,
transforms, scratchpads, settings, and notification sounds.

- **Computer-first & loopback-only.** Binds to `127.0.0.1` only — the loopback
  boundary *is* the auth model. `Host:` header checked on every request as a
  cheap DNS-rebinding defense.
- **Never blocks dictation.** Flask runs in a daemon thread; the window runs in a
  separate process. A crash in either can't wedge the hotkey path.
- **Works offline forever.** No SPA framework, no Node toolchain — server-rendered
  HTML + tiny vanilla JS.

Open it from **Tray → Open Dashboard**, `run_dashboard.bat`, or a browser fallback
at `http://127.0.0.1:8766` if WebView2 isn't present.

---

## Voice command surface *(experimental, off by default)*

Beyond dictation, Echo Flow can act on a spoken **prefix word** (default
`"computer"`). Two layers share the prefix; Command Mode runs first and falls
through to Action Mode on a miss. Both are opt-in under the `experimental:` block.

### Command Mode — keystrokes
Say `"computer, select all"`, `"computer, save"`, `"computer, scroll down"` and Echo
fires the keystroke from an **allowlist** instead of typing the words.

### Action Mode — semantic actions
The same prefix, for actions that reach outside the keyboard. A deliberately
conservative, allowlist-driven catalog:

| Say… | It does |
|---|---|
| "computer, open spotify" | Launches an app from your `action_apps` allowlist (no shell-from-voice, ever) |
| "computer, open github.com" / "go to docs.python.org" | Opens a site (`http`/`https`/`mailto` only) |
| "computer, search the web for …" | Opens a web search |
| "computer, open email" | Opens your configured mail URL |
| "computer, open downloads folder" | Opens a folder from the `action_folders` allowlist |
| "computer, summarize this pdf" | Summarizes the focused document with your **local** model — never a cloud call |
| "computer, create an event lunch with Sam tomorrow" | Writes a local `.ics` **draft** and opens it — never touches a calendar API |
| "computer, take a note that the build is green" | Saves a note |
| Media / volume controls | "play", "pause", "next", "previous", "mute", "volume up/down" via OS media keys |

**Safety model (non-negotiable):** the allowlist and URL-scheme checks are the *sole*
authority on what executes. Nothing in Action Mode deletes, sends, or pays. Every
attempt — success or failure — is logged to the `voice_actions` table, with sensitive
arguments redacted at-rest unless verbose logging is opted into.

---

## Privacy & data flow

This is the whole point, so it's explicit:

- **Local by default.** No telemetry, no analytics, no auto-update phone-home. All
  audio, transcripts, embeddings, and learning data live in `data/history.db` on
  your machine.
- **Cloud is opt-in and gated.** The *only* paths that call a cloud API are
  **Prompt-Engineering mode** (`Ctrl+Shift+Alt`, rewrites a spoken idea into a full
  prompt via Groq) and the optional **teacher-distillation loop**. Both require a key
  you set yourself and both are off until you flip the toggle.
- **No keys are ever logged.** Startup audits which cloud features are enabled and
  warns on a missing key — without printing the key.
- **Bridge & dashboard stay loopback-only** unless you deliberately change the bind
  address.

---

## Platforms

| Surface | Status |
|---|---|
| **Windows desktop** | Primary, full-featured. `INSTALL.bat` for autostart, `run.bat` to launch, `RESTART.bat` after config changes. |
| **iOS** | Custom keyboard extension — hold to dictate, release to insert. Talks to the desktop's local bridge over Wi-Fi, or falls back to on-device Whisper. Build needs a Mac with Xcode (`ios/README.md`). |

---

## Setup at a glance

```bat
scripts\setup.bat        :: creates the venv, installs deps
run.bat                  :: launch (first run downloads the Whisper model)
ollama pull qwen2.5:3b-instruct   :: optional local cleanup LLM (recommended)
```

Health check: `curl http://127.0.0.1:8766/api/healthz` returns daemon liveness, current
phase, and which optional features are wired — without exposing keys.

---

## Maturity

- **Current line:** `0.1.0` (2026-05-20) plus an unreleased line that enforces
  local-only by default and ships the Phase 14 Action Layer.
- **Tests:** 650+ passing, covering dictation, actions, tags, notes, grading,
  snippet expansion, A/B logging, veto behavior, and the action-layer security model.
- **Cost:** nothing when run fully local. Groq is free at single-human speaking
  volumes; the Anthropic/OpenAI cleanup paths cost real money per call and are
  strictly opt-in.

---

## Where to read more

| Doc | Covers |
|---|---|
| [`README.md`](README.md) | Install, hotkeys, configuration, troubleshooting |
| [`docs/DASHBOARD.md`](docs/DASHBOARD.md) | Dashboard architecture, security, and rollout |
| [`CHANGELOG.md`](CHANGELOG.md) | Full feature history by version |
| [`docs/MOBILE_SETUP.md`](docs/MOBILE_SETUP.md) / [`docs/MOBILE_BRIDGE.md`](docs/MOBILE_BRIDGE.md) | iOS pairing and the local bridge |
| [`docs/`](docs/) | Developer specs, audits & roadmaps (e.g. the Phase 14 Action Layer) |

*License: MIT.*
