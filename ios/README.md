# Echo Flow for iOS

A custom keyboard you install once. Long-press the globe key, pick **Echo Flow**, hold the mic button, talk, release. The transcript inserts where your cursor is — in any app, in any text field.

This is the iOS prototype that mirrors the desktop Echo Flow loop (record → Whisper → optional LLM polish → insert), implemented as a keyboard extension because iOS doesn't allow global hotkeys.

## What's in here

```
ios/
├── project.yml              XcodeGen spec — generates the .xcodeproj
├── EchoFlow/                Host app (settings, mic permission, install guide)
├── EchoFlowKeyboard/        Keyboard extension (the actual keyboard)
└── EchoFlowCore/            Shared Swift sources compiled into both targets
    ├── SharedConfig.swift       App-Group UserDefaults wrapper
    ├── AudioRecorder.swift      AVAudioRecorder wrapper, 16 kHz mono m4a
    ├── Transcriber.swift        Protocol + Groq→on-device fallback chain
    ├── GroqTranscriber.swift    Multipart POST to Groq's Whisper endpoint
    ├── WhisperLocalTranscriber  WhisperKit wrapper (on-device, downloads model)
    └── Cleanup.swift            Snippet expansion + Groq LLM polish
```

## Build (one-time)

You need a Mac with Xcode 15+ and an Apple Developer account (free tier is fine for sideloading to your own device).

```sh
brew install xcodegen
cd ios
xcodegen generate
open EchoFlow.xcodeproj
```

In Xcode:
1. Select the **EchoFlow** target, **Signing & Capabilities** tab. Pick your Team. Xcode will auto-generate provisioning profiles for the app and the keyboard extension.
2. The bundle identifiers `com.echoflow.app` and `com.echoflow.app.keyboard` may already be taken on Apple's side — change them to something unique (e.g. `com.yourname.echoflow*`). Update the App Group identifier (`group.com.echoflow.shared`) to match in **both** targets' entitlements and in `EchoFlowCore/SharedConfig.swift`.
3. Plug in your iPhone, pick it as the run destination, hit Run.

## Install on your phone

After the host app launches once:

1. In the Echo Flow app: tap **Allow** next to Microphone. This is what authorizes the keyboard extension to record audio later.
2. Paste your Groq API key (free at [console.groq.com](https://console.groq.com)). Optional — without it, transcription falls back to on-device Whisper.
3. Tap **How to install the keyboard** for the iOS-Settings walkthrough. The short version:
   - Settings → General → Keyboard → Keyboards → **Add New Keyboard…** → Echo Flow
   - Tap the new Echo Flow row → enable **Allow Full Access** (required for mic + network from a keyboard extension)
4. Open any text field anywhere on the phone. Long-press the globe (🌐) key, choose Echo Flow. Hold the mic, talk, release.

## How transcription falls back

`SharedConfig.backend` defaults to `groqWithFallback`:

- **Groq** is tried first. ~200ms latency, requires network + API key.
- **On-device WhisperKit** runs if Groq fails (no network, rate-limit, no key). The model (`openai_whisper-tiny.en`, ~75 MB) downloads on first use to the app's caches directory. Tap **Download** in the host app's "On-device model" section to prefetch it over Wi-Fi.

You can pin a single backend via the picker (Groq only / on-device only) if you don't want fallback behavior.

## How cleanup works

Mirrors the desktop's two-pass cleanup:

1. **LLM polish** (optional): a fast Groq chat call (`llama-3.1-8b-instant`) fixes punctuation and obvious transcription errors without paraphrasing. Guarded against hallucination by a length-ratio check (output must be 0.5–3× input length).
2. **Snippet expansion**: case-aware word-boundary replacement using the same list as `config.yaml` (btw → by the way, etc.).

Both are toggleable in the host app.

## Known limitations vs. desktop

- **No learned/RAG cleanup.** The desktop's `learn.py` + `retrieval.py` (past corrections feed back as few-shot examples) isn't ported. Would need a sync mechanism between the keyboard extension's brief lifetime and the host app's storage.
- **No history view or knowledge graph.** The keyboard extension is a short-lived process — wiring SQLite into it adds risk without much UX value for a v0.1.
- **No profile-by-app cleanup.** Keyboard extensions don't know which host app is using them beyond the `UITextInputTraits` (which gives keyboard type, not app identity).
- **Memory budget.** Keyboard extensions have a tight memory ceiling (historically ~70 MB; higher on modern iOS but still constrained). `tiny.en` fits; `base` is borderline. Don't try `small` or larger.
- **Latency.** First Groq call after a cold start has TLS handshake overhead (~300–500 ms). Subsequent calls reuse the URLSession and feel snappier.

## When the keyboard doesn't seem to work

- **Mic button does nothing, no error.** "Allow Full Access" is probably off. Settings → General → Keyboard → Keyboards → Echo Flow → toggle on.
- **"Mic permission denied — open Echo Flow app".** The host app never had its mic permission granted, so the keyboard inherits a denial. Open the host app, tap Allow under Microphone.
- **"Both transcription backends failed".** Usually no Groq key and on-device model not downloaded. Either add a key, or open the host app and tap Download under "On-device model" while on Wi-Fi.
- **Slow first transcription.** First WhisperKit invocation loads CoreML weights into memory (~1–2s on a modern device). Subsequent calls are fast.

## Why XcodeGen instead of a committed `.xcodeproj`

`project.pbxproj` is a UUID-keyed format that does not diff cleanly and is unforgiving of typos. `project.yml` is human-readable, and `xcodegen generate` produces a consistent, working project every time. It's a one-line install and one extra command. Worth it.
