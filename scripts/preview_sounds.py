"""Play each available sound option so you can pick your favorites.

Run:  .venv\Scripts\python.exe preview_sounds.py

After you decide, edit config.yaml under `sound:`:
    start_alias: "SystemAsterisk"
    stop_alias:  "SystemDefault"

You can also use a custom .wav file:
    start_alias: "C:/path/to/your/sound.wav"
(absolute path, forward slashes, must be a .wav file)
"""
import sys
import time
import winsound

# Built-in Windows system sound aliases — these always play through your speakers
ALIASES = [
    ("SystemAsterisk",     "ding (information sound)"),
    ("SystemDefault",      "default beep"),
    ("SystemExclamation",  "warning / exclamation"),
    ("SystemHand",         "critical error stop sound"),
    ("SystemQuestion",     "question dialog sound"),
    (".Default",           "Windows default event"),
]

# Optional: common .wav files included with Windows you can point to
WAV_PATHS = [
    r"C:\Windows\Media\chimes.wav",
    r"C:\Windows\Media\chord.wav",
    r"C:\Windows\Media\ding.wav",
    r"C:\Windows\Media\notify.wav",
    r"C:\Windows\Media\tada.wav",
    r"C:\Windows\Media\Windows Notify Calendar.wav",
    r"C:\Windows\Media\Windows Notify Messaging.wav",
    r"C:\Windows\Media\Windows Background.wav",
    r"C:\Windows\Media\Windows Foreground.wav",
    r"C:\Windows\Media\Windows Pop-up Blocked.wav",
]


def play_alias(alias: str) -> None:
    winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT)


def play_wav(path: str) -> bool:
    import os
    if not os.path.exists(path):
        return False
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
    return True


print("=" * 60)
print("Sound preview — listen and pick your favorites")
print("=" * 60)

print("\n--- BUILT-IN ALIASES ---")
for alias, desc in ALIASES:
    print(f"  {alias:25s}  ({desc})")
    play_alias(alias)
    time.sleep(1.2)

print("\n--- WAV FILES (longer / fancier) ---")
for path in WAV_PATHS:
    name = path.rsplit("\\", 1)[-1]
    print(f"  {name}")
    if not play_wav(path):
        print(f"    (not found, skipping)")
        continue
    time.sleep(2.0)

print("\n" + "=" * 60)
print("Done. Pick two:")
print("  - one short sound for START (press)")
print("  - one short sound for STOP  (release)")
print()
print("Then edit config.yaml under 'sound:' and set:")
print('    start_alias: "<your choice>"')
print('    stop_alias:  "<your choice>"')
print()
print("For built-in aliases use the name as-is (e.g. SystemAsterisk).")
print('For .wav files use forward slashes: "C:/Windows/Media/ding.wav"')
print("Then run RESTART.bat.")
