# winget packaging

Manifests for publishing Echo Flow to the
[Windows Package Manager Community Repository](https://github.com/microsoft/winget-pkgs),
so users can install with:

```powershell
winget install JOhnsonKC201.EchoFlow
```

Package identifier: **`JOhnsonKC201.EchoFlow`**. Manifests live under
`JOhnsonKC201.EchoFlow/<version>/` and follow the multi-file schema (version +
installer + locale), manifest schema **1.6.0**.

> ⚠️ The checked-in manifest is a **template**: `InstallerSha256` is a
> placeholder (64 zeros) and the URL/version point at a release that may not
> exist yet. It validates against the schema but must be filled with real
> values before submission.

## Per-release update

After the `release` workflow publishes a GitHub Release:

1. **Copy the version folder.** Duplicate the latest `JOhnsonKC201.EchoFlow/<old>/`
   to `JOhnsonKC201.EchoFlow/<new>/`.
2. **Edit the three files** for the new version:
   - `JOhnsonKC201.EchoFlow.yaml` → `PackageVersion`
   - `JOhnsonKC201.EchoFlow.locale.en-US.yaml` → `PackageVersion`
   - `JOhnsonKC201.EchoFlow.installer.yaml` → `PackageVersion`, `ReleaseDate`,
     and in `Installers[0]`: `InstallerUrl` (the release asset download URL) and
     `InstallerSha256` (printed in the release job's **summary**, or from the
     uploaded `*.sha256` file).
3. **Validate locally** (Windows, with winget installed):
   ```powershell
   winget validate --manifest packaging\winget\JOhnsonKC201.EchoFlow\<new>
   # Optional end-to-end install test in a sandbox:
   winget install --manifest packaging\winget\JOhnsonKC201.EchoFlow\<new>
   ```

## Submitting to microsoft/winget-pkgs

Easiest path is [`wingetcreate`](https://github.com/microsoft/winget-create):

```powershell
winget install Microsoft.WingetCreate
wingetcreate update JOhnsonKC201.EchoFlow `
  --version <new> `
  --urls https://github.com/JOhnsonKC201/Echo_FLOW/releases/download/v<new>/EchoFlow-Daemon-Setup-<new>.exe `
  --submit
```

`wingetcreate` downloads the asset, computes the real SHA256, regenerates the
manifest, and opens the PR to `microsoft/winget-pkgs` for you. The manifests
here mirror what it produces so they can also be submitted by hand via a fork +
PR if preferred.

### Notes / gotchas

- **Signing.** Microsoft accepts unsigned installers, but an unsigned package
  shows a SmartScreen prompt on install and may draw extra reviewer scrutiny.
  Signing is wired as an optional step in `.github/workflows/release.yml` (see
  `installer/SIGNING.md`) — add the cert secret and it activates.
- **First submission** creates a new package; the moderation bot runs an
  automated install/uninstall in a sandbox, so the SHA256 and URL must be
  correct and the asset must be publicly downloadable (publish the draft
  release first).
- **`ProductCode`** in the installer manifest is Inno's per-user uninstall key
  `{AppId}_is1`. If the `AppId` in `installer/EchoFlow-Daemon.iss` ever changes,
  update it here too or winget upgrades won't correlate.
