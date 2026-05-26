# Echo Flow Installer

Builds `EchoFlow-Setup.exe` — a per-user Windows installer with Start Menu
shortcut, optional desktop shortcut, optional auto-launch on login, and a
clean uninstaller. No admin rights required for end-users.

## Prerequisites

1. **Build the app first** — either:
   - PyInstaller: `python -m PyInstaller EchoFlow.spec --noconfirm`
     -> `dist\EchoFlow\` (default `SourceDir` in the .iss)
   - Nuitka: `.\build_nuitka.ps1`
     -> `dist_nuitka\app.dist\` (uncomment the alt `SourceDir` line)

2. **Install Inno Setup 6** (one-time):
   - Download from https://jrsoftware.org/isdl.php
   - Run `innosetup-6.x.x.exe` and accept defaults.
   - This installs `iscc.exe` (the command-line compiler) at
     `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`.

## Build the installer

From the repo root:

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\EchoFlow.iss
```

Or, if Inno Setup is on PATH:

```powershell
iscc installer\EchoFlow.iss
```

Output: `installer\Output\EchoFlow-Setup.exe` (single file, ~30-50MB).

## What the installer does

- Installs to `%LOCALAPPDATA%\EchoFlow` (no admin, no UAC).
- Adds Start Menu entry under `Echo Flow`.
- Offers checkboxes for:
  - **Desktop shortcut** (unchecked by default)
  - **Launch on sign-in** (unchecked by default; writes
    `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\Echo Flow`)
- Registers an uninstaller in Add/Remove Programs.
- Cleans up `logs\` and `__pycache__\` on uninstall.

## Switching between PyInstaller and Nuitka output

Edit the `SourceDir` define block near the top of `EchoFlow.iss`:

```inno
#define SourceDir       "..\dist\EchoFlow"          ; PyInstaller
; #define SourceDir     "..\dist_nuitka\app.dist"   ; Nuitka
```

## Version bumps

Edit `MyAppVersion` in `EchoFlow.iss`. The `AppId` GUID must stay constant
across versions so upgrades replace the existing install cleanly.

## Code signing

See `SIGNING.md` for cheap certificate options and how to wire `signtool`
into the Inno Setup build.
