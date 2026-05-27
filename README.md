# Echo Flow

A dictation app I built for my own machine because the commercial ones charge monthly fees and send my audio to their servers. This one runs entirely on your computer if you want it to.

Hold Ctrl+Shift, talk, release. The text shows up wherever your cursor is.

## Setup (Windows)

```
scripts\setup.bat
run.bat
```

The setup script creates a Python venv and installs the dependencies. First launch takes a minute or two while Whisper downloads its model.

You'll see a green microphone in your system tray when it's ready.

### If you want it faster (optional)

Grab a free Groq API key from https://console.groq.com (no credit card needed) and:

```
setx GROQ_API_KEY "gsk_..."
```

Close and reopen your terminal so the variable loads. Groq runs Whisper in the cloud and it's about 3x faster than my laptop's CPU. Default config is already wired for it, and it'll fall back to local if you're offline.

### If you want it completely offline

Install Ollama from https://ollama.com, then:

```
ollama pull qwen2.5:3b-instruct
```

Open `config.yaml` and set `whisper.backend: local` and `cleanup.provider: ollama`. That's it, no internet needed after that.

## Using it

- **Ctrl+Shift** (hold): record, then release to paste
- **Ctrl+Shift+Win** (hold, release): re-paste the last dictation somewhere else. Useful when you said something in Slack and want the same text in an email.
- **Tray icon**: pause, edit the last dictation, open the review queue, see your history, view a knowledge graph of past dictations
- **Snippets**: say "btw" and it becomes "by the way", "lgtm" becomes "looks good to me". Edit the list in `config.yaml` under `cleanup.snippets`.

It learns as you go. Every time you correct a dictation via the tray menu, that correction feeds back into the cleanup prompt. After a couple hundred dictations it knows your jargon, your names, and the way you tend to write.

## Configuration

Everything's in `config.yaml`. The interesting knobs:

- `hotkey.combo`: defaults to `ctrl+shift`. Change if it clashes with something.
- `whisper.model`: `tiny`, `base`, `small`, `medium`, `large-v3-turbo`. Bigger = more accurate but slower. `auto` picks one based on whether you have a GPU.
- `cleanup.provider`: `groq` for cloud, `ollama` for local LLM, `none` to skip cleanup and paste Whisper's raw output, `learned` for the LLM-free mode that uses your past corrections.
- `cleanup.profiles`: switches cleanup style based on which app is focused. Slack messages get casual punctuation, VS Code gets symbol-aware cleanup, Gmail gets fuller sentences.

## What's in the folder

```
src/         the actual app
tests/       run with scripts\run_tests.bat
data/        your history.db lives here, and the knowledge graph HTML
logs/        debug output
scripts/     setup, helpers, utility scripts you'll rarely need
ios/         iOS keyboard-extension port — see ios/README.md
config.yaml  the only thing you should normally edit
```

## iOS

There's an iOS version too — a custom keyboard you install via Settings, hold to dictate, release to insert. Same Groq + on-device Whisper fallback as the desktop. See [`ios/README.md`](ios/README.md) for build steps (needs a Mac with Xcode).

The main entry points: `INSTALL.bat` for first-time setup with autostart, `run.bat` to launch manually, `RESTART.bat` to kill and relaunch (useful after config changes), `UNINSTALL.bat` to remove the autostart shortcut and optionally wipe data.

## Things that broke for me at some point

- **Whisper hallucinating "thank you for watching" on silence**: there's a length+RMS guard now; short or quiet clips get dropped.
- **Recording starts when I just want to re-paste**: the Ctrl+Shift+Win combo has a veto. If you add Win within a frame of pressing Ctrl+Shift, the recording aborts silently and the paste fires instead.
- **Ollama "connection refused"**: start the Ollama desktop app or run `ollama serve` in a terminal.
- **Hotkey doesn't work after Windows update**: sometimes pynput's global listener needs the app restarted. `RESTART.bat`.
- **Pasting in some Electron apps lags**: the clipboard restore happens in a background thread. Usually fine, occasionally a 100ms hiccup.
- **Every word getting comma-separated** (`"Hello, World, Today."`): caused by Whisper's `initial_prompt` style-anchoring on a comma-list of vocabulary terms. Fixed in May 2026 — vocabulary is now wrapped in prose, and `_polish_text` has a comma-storm detector as a safety net. If you upgraded and still see it, `RESTART.bat` the daemon so the new `initial_prompt` builder runs.

## Teacher-model distillation (optional)

After each dictation, Echo Flow can re-clean the raw text via a stronger cloud LLM in the background and store the result as a `source='teacher'` row. The pattern miner learns from both your edits and the teacher's, so the system improves toward a reference model — not just toward you.

```
setx GROQ_API_KEY "gsk_..."        # one-time
```

Then open the dashboard → **Settings → Vibe → Teacher model** and flip "Enable teacher-model distillation". Zero added latency on the live dictation path (the teacher runs in a daemon thread). A quality gate compares the teacher's output to yours and only persists the pair when the teacher grades at least as well.

To bootstrap from your existing history (no waiting for new dictations):

```
python scripts\backfill_teacher.py --apply --limit 500
```

Review the pairs at `http://127.0.0.1:8766/teacher` before you trust the loop wholesale.

## Privacy & data flow

- **Local by default.** No telemetry, no analytics, no auto-update phone-home. All audio, transcripts, embeddings, and learning data live in `data/history.db` on your machine.
- **Cloud features are opt-in and explicitly gated.** Prompt-Engineering mode (Ctrl+Shift+Alt) and the teacher loop are the only paths that call a cloud API. Both require an API key you set yourself and both are off until you flip the toggle.
- **Bridge stays loopback-only** unless you change `mobile.bind_address`. Read `MOBILE_BRIDGE.md` before exposing to LAN.
- **Dashboard stays loopback-only** on `127.0.0.1:8766`. Same trust model as local browser tabs.
- **No keys are ever logged.** Startup audits which cloud features are enabled and warns if their key is missing, without printing the key itself.

## Health check

```
curl http://127.0.0.1:8766/healthz
```

Returns daemon liveness, current phase, and which optional features are wired (without exposing keys). Useful for tray watchdogs and installers.

## License

MIT — see [`LICENSE`](LICENSE).

## What it costs

Nothing if you run it fully local. Groq is free at the volumes a single human can talk. Anthropic and OpenAI cost real money per API call, so only use them if you want their cleanup quality and don't mind the bill.

