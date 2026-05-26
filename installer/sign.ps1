<#
.SYNOPSIS
    Authenticode-sign all Echo Flow distributable artifacts with signtool.

.DESCRIPTION
    Signs (in this order):
      1. dist\EchoFlow\EchoFlow.exe                 (dashboard shell)
      2. dist\EchoFlow-Daemon\EchoFlow-Daemon.exe   (background daemon)
      3. installer\Output\EchoFlow-Setup-*.exe
      4. installer\Output\EchoFlow-Daemon-Setup-*.exe

    Uses SHA256 file digest + RFC 3161 timestamp from DigiCert. Run BEFORE
    handing the installers to users so SmartScreen and AV vendors see a
    valid publisher signature.

.PARAMETER PfxPath
    Path to the .pfx code-signing certificate (EV or OV).

.PARAMETER PfxPassword
    Password protecting the .pfx. Provide as SecureString in CI, or plaintext
    locally — caller's choice.

.PARAMETER SignToolPath
    Optional explicit path to signtool.exe. Defaults to the standard
    Windows SDK location; PATH lookup is attempted as a fallback.

.EXAMPLE
    .\installer\sign.ps1 -PfxPath C:\secrets\echoflow.pfx -PfxPassword 'hunter2'

.NOTES
    - Run AFTER build_all.ps1 but BEFORE `iscc` if you want signed exes
      inside the installer payload. Run AGAIN after `iscc` to sign the
      installer itself.
    - For the Inno-integrated path, configure SignTool inside Inno Setup
      and uncomment the `SignTool=` directives in both .iss files.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$PfxPath,

    [Parameter(Mandatory = $true)]
    [string]$PfxPassword,

    [string]$SignToolPath = "",

    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $repoRoot

# ---------------------------------------------------------------------------
# Locate signtool.exe
# ---------------------------------------------------------------------------
function Find-SignTool {
    param([string]$Hint)

    if ($Hint -and (Test-Path $Hint)) { return $Hint }

    $candidates = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin\x64\signtool.exe",
        "${env:ProgramFiles(x86)}\Windows Kits\10\App Certification Kit\signtool.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }

    # Search every installed SDK build for an x64 signtool.exe.
    $sdkBase = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
    if (Test-Path $sdkBase) {
        $found = Get-ChildItem $sdkBase -Recurse -Filter "signtool.exe" -ErrorAction SilentlyContinue |
                 Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } |
                 Sort-Object FullName -Descending |
                 Select-Object -First 1
        if ($found) { return $found.FullName }
    }

    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    throw "signtool.exe not found. Install the Windows 10/11 SDK or pass -SignToolPath."
}

$signtool = Find-SignTool -Hint $SignToolPath
Write-Host "Using signtool: $signtool" -ForegroundColor Cyan

if (-not (Test-Path $PfxPath)) {
    throw "PFX not found at: $PfxPath"
}

# ---------------------------------------------------------------------------
# Sign a single file with SHA256 + RFC 3161 timestamp.
# ---------------------------------------------------------------------------
function Sign-One {
    param([string]$File)

    if (-not (Test-Path $File)) {
        Write-Host " [skip] $File (not present)" -ForegroundColor Yellow
        return
    }

    Write-Host " signing: $File" -ForegroundColor Green
    & $signtool sign `
        /fd SHA256 `
        /tr $TimestampUrl `
        /td SHA256 `
        /f  $PfxPath `
        /p  $PfxPassword `
        $File

    if ($LASTEXITCODE -ne 0) {
        throw "signtool failed (exit $LASTEXITCODE) for $File"
    }

    & $signtool verify /pa /v $File | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Signature verification failed for $File"
    }
}

# ---------------------------------------------------------------------------
# Targets — bundled exes first, then any built installers found.
# ---------------------------------------------------------------------------
$targets = @(
    "dist\EchoFlow\EchoFlow.exe",
    "dist\EchoFlow-Daemon\EchoFlow-Daemon.exe"
)

if (Test-Path "installer\Output") {
    $targets += (Get-ChildItem "installer\Output" -Filter "*.exe" -ErrorAction SilentlyContinue |
                 ForEach-Object { $_.FullName })
}

foreach ($t in $targets) { Sign-One -File $t }

Write-Host ""
Write-Host "All available artifacts signed successfully." -ForegroundColor Green
