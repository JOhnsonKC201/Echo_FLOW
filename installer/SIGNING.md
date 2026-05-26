# Code Signing — Echo Flow

Why sign? Windows SmartScreen and Smart App Control (SAC) treat unsigned
binaries as untrusted by default. A signed binary from a recognized CA gets
through SmartScreen immediately (with EV certs) or after a small reputation
ramp (with OV certs). Without a signature, SAC will continue to block
rebuilds even after Nuitka shifts the bootloader fingerprint.

## Certificate options (cheapest first)

| Provider                | Type          | Approx. price | Notes                                                                                  |
|-------------------------|---------------|---------------|----------------------------------------------------------------------------------------|
| **Certum Open Source**  | OV (FOSS)     | **~$30/yr**   | Cheapest legitimate option. Requires the project be open-source. Issued on a smartcard / cloud HSM (post-June 2023 CA/B baseline). |
| **SSL.com OV**          | OV            | ~$200/yr      | Cloud signing (eSigner) supported — no physical token shipping. Easiest CI integration. |
| **Sectigo OV**          | OV            | ~$200-300/yr  | Resold cheaply via Cheapsslsecurity / Comodosslstore. Hardware token ships from US/UK.  |
| **DigiCert / Sectigo EV** | EV          | ~$300-600/yr  | Instant SmartScreen reputation. Required for kernel drivers. Always hardware-token.    |

**Recommendation for Echo Flow:** start with **Certum Open Source** (if the
repo is public) — it satisfies SAC/SmartScreen the same way as Sectigo OV
after a brief reputation period.

> Note: as of June 2023, **all** code-signing certs (OV and EV) must ship on
> a FIPS-140-2 Level 2 HSM. You will receive either a USB token (YubiKey /
> SafeNet eToken) or cloud-HSM credentials. The legacy "download a PFX"
> flow no longer exists for new issuances.

## What you receive from the CA

- **Cloud HSM (SSL.com eSigner, Certum SimplySign):** username, password,
  TOTP secret, and a `.cer` (public cert). You sign by calling the CA's
  signing service — `signtool` works via a CSP plugin.
- **Hardware token:** USB device, an installer for its CSP/middleware
  (SafeNet Authentication Client, Certum proCertum), and a PIN. The cert
  appears in `certmgr.msc` once the token is plugged in. `signtool` finds
  it via `/a` (auto-select) or `/n "Subject Name"`.

(Legacy/internal-only PFX flow, for reference: a `.pfx` file + a password,
loaded via `signtool sign /f cert.pfx /p <password>`.)

## Signing with `signtool`

`signtool` ships with the Windows 10/11 SDK at
`C:\Program Files (x86)\Windows Kits\10\bin\<version>\x64\signtool.exe`.

### Sign the main binary

```powershell
signtool sign `
    /tr http://timestamp.digicert.com `
    /td sha256 `
    /fd sha256 `
    /a `
    dist\EchoFlow\EchoFlow.exe
```

Flag breakdown:
- `/tr <url>` — RFC 3161 timestamp server. Without this, the signature
  expires when the certificate does. Free alternates:
  `http://timestamp.sectigo.com`, `http://timestamp.globalsign.com/tsa/r6advanced1`.
- `/td sha256` — digest algorithm for the timestamp request.
- `/fd sha256` — file digest algorithm (SHA-1 is deprecated).
- `/a` — auto-select the best cert from the user's store. Use
  `/n "Echo Flow"` or `/sha1 <thumbprint>` to disambiguate if you have
  multiple certs.

### Sign the installer too

After Inno Setup builds `EchoFlow-Setup.exe`:

```powershell
signtool sign /tr http://timestamp.digicert.com /td sha256 /fd sha256 /a installer\Output\EchoFlow-Setup.exe
```

### Verify

```powershell
signtool verify /pa /v dist\EchoFlow\EchoFlow.exe
```

## Wiring it into Inno Setup

Inno Setup can call `signtool` automatically — both on bundled binaries
and on the generated setup .exe (and its uninstaller).

1. **Register the signtool command** once (per machine), via the Inno IDE
   menu *Tools -> Configure Sign Tools* — or from the CLI:

   ```
   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /Ssigntool=$qC:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\signtool.exe$q sign /tr http://timestamp.digicert.com /td sha256 /fd sha256 /a $f
   ```

   `$f` is the placeholder Inno replaces with the file path; `$q` is a
   literal quote.

2. **Enable signing in `EchoFlow.iss`** by uncommenting:

   ```inno
   [Setup]
   SignTool=signtool
   SignedUninstaller=yes
   ```

   With these in place, every file listed in `[Files]` (and the final
   `EchoFlow-Setup.exe`, and the embedded uninstaller) is signed during
   the `iscc` build.

3. **Per-file signing** (if you only want the main exe signed, not every
   DLL — useful with hardware tokens that prompt per signature):

   ```inno
   [Files]
   Source: "..\dist\EchoFlow\EchoFlow.exe"; DestDir: "{app}"; Flags: sign
   Source: "..\dist\EchoFlow\*";            DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
   ```

## Reputation note

Even a perfectly signed OV binary will trigger a one-time SmartScreen
"unrecognized publisher" prompt for the first ~100 downloads. EV certs
skip this entirely. After SmartScreen reputation is built, SAC also
silently allows the binary. Plan for either:
- a 1-2 week ramp on OV, or
- pay the EV premium for day-one trust.
