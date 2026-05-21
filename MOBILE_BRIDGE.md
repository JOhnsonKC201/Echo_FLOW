# Mobile Bridge: use Echo Flow from your iPhone or Android over Wi-Fi

The mobile dictation apps (Apple Dictation on iOS, FUTO Voice on Android) already do good local transcription. What they don't do is **Echo Flow's cleanup and learning** — your snippets, learned corrections, app-aware profile, and history.

The bridge fixes that. Your PC runs the brain, your phone is a thin remote. One Wi-Fi network, one HTTP call, cleaned text on your clipboard.

If you only want phone dictation without the cleanup layer, [MOBILE_SETUP.md](MOBILE_SETUP.md) covers that — it's zero-effort and a fine stop.

## What it does

1. Phone records a few seconds of audio (iOS Shortcut, Android Tasker, or any HTTP client).
2. Phone POSTs the WAV to `http://<your-pc>:8765/v1/dictate` over your home Wi-Fi.
3. PC runs the same Whisper + Cleaner the desktop hotkey runs. Logs it to `history.db`. Feeds it to the learner.
4. PC returns `{"cleaned": "...", "raw": "...", ...}`.
5. Phone copies the cleaned text to the clipboard. Paste anywhere.

Nothing leaves your LAN. If your Echo Flow is configured for fully local mode (`whisper.backend: local` + `cleanup.provider: ollama`), the bridge also runs fully offline. If you opted into Groq, the bridge inherits that.

## One-time setup on the PC

1. Pull the latest Echo Flow and run `scripts\setup.bat` (or `pip install -r requirements.txt` in your venv) — this picks up the new `flask` and `zeroconf` deps.
2. Open `config.yaml` and set:
   ```yaml
   mobile:
     enabled: true
   ```
3. Launch Echo Flow. On first run the console prints a line like:
   ```
   Mobile bridge: http://192.168.1.42:8765  key=Yk3p2x…  (first run? allow Python through Windows Firewall)
   Mobile shared key: Yk3p2xQ-r9wH6dT8sQEqLp1mZcRkB4nVo7Tj
   ```
   Copy the **full** shared key — you'll paste it into the phone in the next section.
4. Windows will show a **Windows Defender Firewall** popup the first time the server binds to `0.0.0.0`. Click **Allow access** for both Private and Public-meaning-Home networks. If you accidentally dismissed it, see the Troubleshooting section.

Re-launches don't regenerate the key; it's persisted into `config.yaml`. To rotate, clear the `shared_key:` value and restart.

## iPhone — Shortcuts recipe (no app install)

7 actions. Build it once, bind it to the Action Button or back-tap.

1. **Record Audio** — set "Stop recording" to **On Tap**.
2. **Encode Media** — input the recording, set Audio Format to **WAV** (this is critical; the bridge accepts PCM16 mono WAV only).
3. **Text** action — paste your shared key here. This stays inside the Shortcut, not in iCloud.
4. **Get Contents of URL**:
   - URL: `http://echoflow.local:8765/v1/dictate?source=iOS` *(if mDNS doesn't resolve on your network, use `http://<pc-ip>:8765/v1/dictate?source=iOS`)*
   - Method: **POST**
   - Headers: `X-Echo-Key` → set to the **Text** variable from step 3
   - Request Body: **Form**
     - Add field name `file`, type **File**, value = the **Encoded Media** from step 2
5. **Get Dictionary Value** — Get value for `cleaned` from the previous step's output.
6. **Copy to Clipboard** — input the dictionary value.
7. **Show Notification** — body = the dictionary value. (Optional but reassuring.)

Save as `Dictate via Echo Flow`. Set it as your Action Button shortcut or assign back-tap (Settings → Accessibility → Touch → Back Tap).

**Test it:** open Notes, press your assigned trigger, speak for 3 seconds, paste with Cmd-V (or long-press). Cleaned text should appear.

## Android — Tasker recipe

Tasker (one-time paid app) or any HTTP automation tool will work. Steps:

1. **Record Audio** — file format WAV, mono, 16 kHz, save to `/sdcard/Tasker/echo.wav`.
2. **HTTP Request**:
   - Method: **POST**
   - URL: `http://<pc-ip>:8765/v1/dictate?source=Android`
   - Headers: `X-Echo-Key: <your shared key>`
   - File to send: `/sdcard/Tasker/echo.wav`
   - MIME type: `audio/wav`
3. **Variable Set** — `%cleaned` to `%http_data` parsed as JSON, field `cleaned`. (In Tasker: use the **JavaScriptlet** `var d = JSON.parse(global('http_data')); setLocal('cleaned', d.cleaned);`)
4. **Set Clipboard** — `%cleaned`.

Bind it to a Quick Settings tile, a home-screen shortcut, or a Bluetooth-button trigger.

**Note on mDNS on Android:** Android does not resolve `.local` system-wide. Use the PC's IP address.

## Quick smoke test from any laptop on the LAN

```bash
# Discover health (no auth needed)
curl http://<pc-ip>:8765/v1/health

# Send a WAV (record one with QuickTime / Audacity / sox first)
curl -H "X-Echo-Key: <your-key>" \
     -F "file=@sample.wav" \
     "http://<pc-ip>:8765/v1/dictate?source=test"

# Clean already-transcribed text (no audio)
curl -H "X-Echo-Key: <your-key>" \
     -H "Content-Type: application/json" \
     -d '{"text":"um yeah so like the meeting is at 3","style":"casual"}' \
     http://<pc-ip>:8765/v1/cleanup
```

## API reference

| Endpoint            | Auth | Body                              | Returns                                                |
|---------------------|------|-----------------------------------|--------------------------------------------------------|
| `GET  /v1/health`   | no   | —                                 | `{ok, phase, providers, model_loaded}`                 |
| `POST /v1/transcribe` | yes | multipart `file` = WAV          | `{text, language, ms}`                                 |
| `POST /v1/cleanup`  | yes  | JSON `{text, style?}`             | `{text, style}`                                        |
| `POST /v1/dictate`  | yes  | multipart `file` = WAV, `?style=`, `?source=` | `{raw, cleaned, language, style, source, ms, ...}` |
| `GET  /v1/history`  | yes  | `?limit=N` (default 20, max 200) | `{items: [{ts, window_title, style, cleaned}, ...]}`   |

Auth = send `X-Echo-Key: <shared key>` header. Wrong or missing key returns `401`. Audio that's too short (<400ms) or too quiet (RMS<0.003) returns `200` with `{"text":"","reason":"too_short"}` so your Shortcut doesn't error-popup on accidental triggers. Non-PCM16 WAV returns `415`.

## Troubleshooting

- **Phone says "Connection refused" or times out.** Windows Defender Firewall blocked the listener on first run. Open *Allowed apps* (Win+R → `firewall.cpl` → "Allow an app or feature") and ensure `python.exe` (the one in your venv) is checked for Private networks. Restart Echo Flow.
- **iPhone Safari shows the `/health` JSON but Shortcut fails.** Usually a WAV-format mismatch. Confirm the **Encode Media** step has Audio Format = WAV (not M4A/AAC). If Apple changed the WAV sub-format in your iOS version, the server returns `415` — check the response body in the Shortcut for details.
- **`echoflow.local` doesn't resolve.** Some routers block mDNS. Use the printed IP address directly. On Windows you can run `ipconfig` to confirm the IP.
- **The bridge is up but I get `401 unauthorized`.** Header name is `X-Echo-Key` (case-insensitive). Value must match `mobile.shared_key` in `config.yaml` exactly — no trailing spaces.
- **I want to rotate the key.** Clear `mobile.shared_key:` in `config.yaml` and restart. A new key prints to the console.
- **Phone request blocks the desktop hotkey for a few seconds.** The pipeline lock serializes the shared Whisper model. v1 trade-off; mobile use is bursty so it shouldn't bite often.
- **My PC has multiple network interfaces.** Set `mobile.bind_address` to the specific LAN IP (e.g. `192.168.1.42`) instead of `0.0.0.0`.

## Where to go from here

- The mobile dictations land in the same `history.db` with `window_title="Mobile:iOS"` / `"Mobile:Android"`. They feed the same learner. Corrections you make via the tray's "Edit last" UI also propagate.
- If you want true keyboard integration on Android, see the FUTO Voice section in `MOBILE_SETUP.md`. A future Echo Flow project could be a forked FUTO Voice that POSTs its already-transcribed text to `/v1/cleanup` for the LLM polish step.
- For now, the Shortcut + Tasker combo gives you ~95% of the desktop experience on the go.
