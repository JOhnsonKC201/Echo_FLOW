# Echo Flow — Windows Distribution

Echo Flow ships as **two** installable artifacts. End users should grab the
**daemon installer** — it's the full product. The dashboard-only installer
exists for users who already run the daemon some other way (dev checkout,
`run_silent.vbs`, etc.) and just want the visual shell.

| Installer                                  | What it contains                                   | Who it's for                          |
|--------------------------------------------|----------------------------------------------------|---------------------------------------|
| `EchoFlow-Daemon-Setup-<ver>.exe`          | Daemon + embedded dashboard + Flask + ML stack     | **Most users.** Real install.         |
| `EchoFlow-Setup-<ver>.exe`                 | Dashboard shell only (`app.py` PyInstaller bundle) | Devs / users running the daemon raw   |

Both installers are **per-user** (no admin required), install under
`%LOCALAPPDATA%\Programs\EchoFlow`, and store runtime data under
`%LOCALAPPDATA%\EchoFlow\`.

---

## Build prerequisites

- Python 3.11+ with the project's `.venv` activated and `requirements.txt`
  installed.
- `pyinstaller` in the venv.
- [Inno Setup 6](https://jrsoftware.org/isdl.php) on PATH (`iscc.exe`).
- Optional: Windows 10/11 SDK for `signtool.exe` (only needed if signing).

## Building both `.exe`s

From the repo root:

```powershell
.\.venv\Scripts\Activate.ps1
.\build_all.ps1
```

This runs PyInstaller twice in sequence and reports per-stage timing:

1. `EchoFlow.spec`         -> `dist\EchoFlow\EchoFlow.exe` (dashboard shell)
2. `EchoFlow-Daemon.spec`  -> `dist\EchoFlow-Daemon\EchoFlow-Daemon.exe`

Flags:

- `-Clean`           — pass `--clean` through to PyInstaller.
- `-SkipDashboard`   — only rebuild the daemon.
- `-SkipDaemon`      — only rebuild the dashboard shell.

## Building the installers

```powershell
iscc installer\EchoFlow.iss          # dashboard-only installer
iscc installer\EchoFlow-Daemon.iss   # full-product installer (recommended)
```

Outputs land in `installer\Output\`.

## What the daemon installer wires up

- Per-user install at `%LOCALAPPDATA%\Programs\EchoFlow`.
- Optional **Auto-start on login** task — writes
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\EchoFlow` pointing
  at `EchoFlow-Daemon.exe` (this replaces the dev-grade `run_silent.vbs`
  flow once the user is on the bundled exe).
- Optional **Launch now** task — starts the daemon when setup finishes.
- Optional Start Menu and Desktop shortcuts.
- Uninstall handler that stops a running daemon via PowerShell
  `Stop-Process` before deleting files (so files aren't locked).
- User data under `%LOCALAPPDATA%\EchoFlow\` is **not** removed on
  uninstall.

## ⚠️ Before the daemon build is useful

`src/main.py` must resolve its user-data dir to `%LOCALAPPDATA%\EchoFlow\`
when running under PyInstaller (i.e. `sys.frozen == True`). The dashboard
shell (`app.py`) already does this; the daemon needs the same patch
applied manually. Without it, the frozen daemon will try to write
`config.yaml` and `history.db` next to the bundled `.exe` and fail on a
non-writable install location.

## Code signing

See [`SIGNING.md`](./SIGNING.md) and [`sign.ps1`](./sign.ps1). Short
version:

```powershell
.\build_all.ps1
.\installer\sign.ps1 -PfxPath cert.pfx -PfxPassword '...'
iscc installer\EchoFlow.iss
iscc installer\EchoFlow-Daemon.iss
.\installer\sign.ps1 -PfxPath cert.pfx -PfxPassword '...'
```
