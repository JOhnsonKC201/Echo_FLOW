# Code Signing — Echo Flow Windows Artifacts

Echo Flow ships two PyInstaller-built executables and two Inno Setup
installers. All four should be Authenticode-signed before release so that
Windows SmartScreen, AV vendors, and corporate device policies treat the
download as trusted.

## Artifacts to sign

| Path                                              | What it is                          |
|---------------------------------------------------|-------------------------------------|
| `dist\EchoFlow\EchoFlow.exe`                      | Dashboard shell (lightweight)       |
| `dist\EchoFlow-Daemon\EchoFlow-Daemon.exe`        | Full background daemon              |
| `installer\Output\EchoFlow-Setup-<ver>.exe`       | Dashboard-only installer            |
| `installer\Output\EchoFlow-Daemon-Setup-<ver>.exe`| Full-product installer (recommended)|

## Order of operations

1. `.\build_all.ps1`        — builds both `.exe`s into `dist\`.
2. `.\installer\sign.ps1`   — signs the two bundled exes (so the payload
   carried inside the installer is itself signed).
3. `iscc installer\EchoFlow.iss` and `iscc installer\EchoFlow-Daemon.iss`
   — package the installers.
4. `.\installer\sign.ps1`   — re-run to sign the freshly built `*-Setup-*.exe`s.

If you prefer Inno Setup to invoke `signtool` for you during step 3,
configure a SignTool inside Inno Setup (`Tools -> Configure Sign Tools…`)
and then uncomment the `SignTool=` (and `SignedUninstaller=yes`) lines at
the top of both `.iss` files.

## Certificate

- Use an **OV** or **EV** code-signing certificate from a CA Microsoft
  trusts (DigiCert, Sectigo, GlobalSign, SSL.com, …).
- Export it as a `.pfx` (PKCS#12). Keep the password in a secrets store —
  never commit it.
- EV certs unlock instant SmartScreen reputation; OV certs accumulate
  reputation over downloads.

## Signing parameters used

- Digest:    **SHA256** (`/fd SHA256`)
- Timestamp: **RFC 3161** at `http://timestamp.digicert.com`
  (`/tr <url> /td SHA256`)
- Both bundled exes AND the installers are signed identically.

## CI usage sketch

```powershell
$env:PFX_PASSWORD = $env:CODESIGN_PFX_PWD     # from your secrets store
.\build_all.ps1
.\installer\sign.ps1 -PfxPath $env:CODESIGN_PFX -PfxPassword $env:PFX_PASSWORD
iscc installer\EchoFlow.iss
iscc installer\EchoFlow-Daemon.iss
.\installer\sign.ps1 -PfxPath $env:CODESIGN_PFX -PfxPassword $env:PFX_PASSWORD
```

## Verifying a signature locally

```powershell
signtool verify /pa /v dist\EchoFlow-Daemon\EchoFlow-Daemon.exe
```
