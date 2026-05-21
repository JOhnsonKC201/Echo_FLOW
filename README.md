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

## What it costs

Nothing if you run it fully local. Groq is free at the volumes a single human can talk. Anthropic and OpenAI cost real money per API call, so only use them if you want their cleanup quality and don't mind the bill.

## License

Use it, modify it, share it. No warranty.
