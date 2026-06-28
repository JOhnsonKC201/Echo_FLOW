# Releasing Echo Flow

The whole release is automated by `.github/workflows/release.yml`. You bump one
version string, push a tag, review a draft release, and (optionally) ship to
winget. No local PyInstaller/Inno builds required.

## 1. Bump the version (single source of truth)

The canonical version is **`src/__init__.py`** (`__version__`). The CI release
job fails fast if the pushed tag doesn't match it, so bump it first:

```python
# src/__init__.py
__version__ = "0.2.1"
```

The Inno scripts no longer hardcode the version for releases — CI passes it via
`iscc /DMyAppVersion=<ver>` (the `#define` in the `.iss` is only a fallback for
manual local builds). Update the README badge if you want it exact (cosmetic).

Add a dated section to `CHANGELOG.md` describing the release.

## 2. Tag and push

```bash
git commit -am "chore(release): cut v0.2.1"
git tag v0.2.1
git push origin main --tags
```

The tag push triggers the `release` workflow on a `windows-latest` runner. It:

1. verifies `v0.2.1` == `src/__init__.py` `__version__`,
2. builds the daemon bundle (`EchoFlow-Daemon.spec`) with PyInstaller,
3. signs it **if** the `CODESIGN_PFX_BASE64` secret is set (see below),
4. zips the bundle into the web-installer **payload** (`EchoFlow-Daemon-Payload-<ver>.zip`) and hashes it,
5. builds **two** installers with Inno Setup — the full offline
   `EchoFlow-Daemon-Setup` and the tiny `EchoFlow-Web-Setup` bootstrapper
   (the latter pinned to the payload's URL + SHA256),
6. signs both installers (same secret condition),
7. computes SHA256s and writes `.sha256` sidecars,
8. uploads CI artifacts and creates a **draft** GitHub Release with all three
   assets (full installer, web installer, payload zip) + checksums attached.

> The web installer downloads the payload from the release URL, so that asset
> must be published for it to work end-to-end. Both installers share an AppId,
> so they're interchangeable for the user.

> The daemon bundles the full ML/audio stack (faster-whisper, ctranslate2,
> sentence-transformers, etc.), so the runner build takes ~15–25 min and the
> installer is large (hundreds of MB). Whisper model weights are **not**
> bundled — they download on first launch.

## 3. Review and publish

Open the draft release, sanity-check the auto-generated notes and the attached
`EchoFlow-Daemon-Setup-<ver>.exe`, then **Publish**. (Drafting first lets you
grab the asset URL + SHA256 for winget before the release goes public.)

### Dry run without tagging

Run the workflow via **Actions → release → Run workflow** and pass a version.
It builds and uploads the installer as a CI artifact but creates **no** release.

## 4. Code signing (optional, opt-in)

Unsigned installers work but trip Windows SmartScreen ("More info → Run
anyway") until the publisher builds reputation. To sign automatically:

1. Obtain an OV/EV code-signing cert and export a `.pfx` (see `SIGNING.md`).
2. Base64-encode it and add repo secrets:
   - `CODESIGN_PFX_BASE64` — `[Convert]::ToBase64String([IO.File]::ReadAllBytes("cert.pfx"))`
   - `CODESIGN_PFX_PASSWORD` — the .pfx password
3. That's it — the two signing steps detect the secret and run `installer/sign.ps1`
   automatically; no workflow edits needed.

## 5. winget

After publishing the release, update and submit the manifest under
`packaging/winget/` — see [`packaging/winget/README.md`](../packaging/winget/README.md).
`wingetcreate update JOhnsonKC201.EchoFlow --version <ver> --urls <asset-url> --submit`
is the one-liner.

## Local build (debugging only)

```powershell
.\.venv\Scripts\Activate.ps1
.\build_all.ps1                       # PyInstaller -> dist\
iscc installer\EchoFlow-Daemon.iss    # -> installer\Output\ (uses the .iss fallback version)
```
