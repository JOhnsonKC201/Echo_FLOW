# Mobile Setup: FUTO Voice on Android, Built-in Dictation on iPhone

The closest thing to Echo Flow on your phone, without writing a line of code.

## Android

The voice part and the keyboard part are separate FUTO apps. You probably want just the voice app and keep using whatever keyboard you have.

### Recommended: FUTO Voice Input (keeps your current keyboard)

It installs as a separate voice service. Gboard keeps typing. FUTO handles the mic. Lowest friction, no keyboard change.

1. Open https://voiceinput.futo.org/ on your phone
2. Tap **Direct APK** (about 70 MB). Or open Play Store: https://play.google.com/store/apps/details?id=org.futo.voiceinput
3. Install it. Android may ask permission to install from unknown sources for this one app. Allow it.
4. Open Settings, search "keyboard", and find the on-screen keyboard settings (path varies by phone).
5. Find "FUTO Voice Input" in the voice services list and enable it.
6. In Gboard's own settings: Languages & input, then Gboard, then Voice typing, then set "Voice input service" to "FUTO Voice Input".
7. Open any text field. Long-press the comma key, choose the mic icon. You're now using FUTO's local Whisper for dictation. No internet needed.

### Optional: also install FUTO Keyboard

FUTO Keyboard is a separate fully-offline keyboard. It is currently in Alpha and does NOT include voice input. You install BOTH apps if you want a 100% open-source keyboard plus FUTO voice. Most people don't need this; Gboard plus FUTO Voice is already a good combo.

If you want it anyway:
1. Open https://keyboard.futo.org/ on your phone
2. Install via Play Store or APK (heads-up: alpha software)
3. Settings, search "keyboard", enable FUTO Keyboard
4. Select it as your default keyboard
5. Make sure your FUTO Voice Input is still set as the voice service

## iPhone

Apple already has on-device dictation since iOS 16. You don't need to install anything.

1. Settings, General, Keyboard, Dictation, make sure **Enable Dictation** is ON
2. While you're there: set "Microphone Source" to "Automatic"
3. Open any text field. The mic icon is on the bottom right of the keyboard. Tap, speak, tap again to stop.

The transcription runs on your device. Apple's on-device speech recognition, not cloud. Privacy is good. Quality is decent.

## What you get vs the desktop Echo Flow

| Feature | Desktop Echo Flow | Android FUTO | iPhone built-in |
|---|---|---|---|
| Local transcription | yes | yes | yes |
| LLM cleanup | yes (Ollama / Groq) | no | minimal |
| Snippet expansion (btw becomes "by the way") | yes | no | only iOS shortcuts |
| Learned corrections | yes | no | no |
| Knowledge graph | yes | no | no |
| Re-paste hotkey | yes (Ctrl+Shift+Win) | no | no |
| Internet required | no | no | no |
| Cost | $0 | $0 | $0 |

You're trading the smarts for portability. That's the deal.

## When to come back

If after a week of using FUTO plus iOS dictation you find yourself missing the cleanup ("ugh, my voice notes are raw stream of consciousness"), that's the signal to consider building Echo Flow's cleanup and learning layer on top of FUTO Voice as an Android-only project. Roughly 2 weeks of focused work.

Until then, this gives you most of the value at zero effort.

## Tier 3: bring the desktop smarts to your phone — [MOBILE_BRIDGE.md](MOBILE_BRIDGE.md)

If you have Echo Flow already running on a PC you can reach over Wi-Fi, you can use **its** Whisper + LLM cleanup + learned corrections from your phone via an iOS Shortcut or Android Tasker recipe. Phone records audio, PC does the brain work, cleaned text lands on the phone's clipboard. Same `history.db`, same learning loop, no internet required. See `MOBILE_BRIDGE.md` for the recipes.

## Sources

- [FUTO Voice Input homepage](https://voiceinput.futo.org/)
- [FUTO Voice Input on Google Play](https://play.google.com/store/apps/details?id=org.futo.voiceinput)
- [FUTO Keyboard homepage](https://keyboard.futo.org/) (alpha, does NOT include voice)
- [FUTO Voice source repo](https://github.com/futo-org/voice-input)
