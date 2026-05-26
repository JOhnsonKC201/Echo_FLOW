<#
.SYNOPSIS
    Build both Echo Flow distributable .exes: dashboard shell + daemon.

.DESCRIPTION
    Runs PyInstaller against each of the two specs in sequence and reports
    per-stage timing. Designed to be invoked from the repo root inside an
    activated virtualenv that already has pyinstaller + all runtime deps.

    Stage 1: dashboard shell  (EchoFlow.spec        -> dist\EchoFlow\)
    Stage 2: full daemon      (EchoFlow-Daemon.spec -> dist\EchoFlow-Daemon\)

.EXAMPLE
    .\.venv\Scripts\Activate.ps1
    .\build_all.ps1

.NOTES
    This script does NOT invoke Inno Setup. After it finishes, build the
    installers separately:
        iscc installer\EchoFlow.iss
        iscc installer\EchoFlow-Daemon.iss
#>

param(
    [switch]$Clean,
    [switch]$SkipDashboard,
    [switch]$SkipDaemon
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

function Invoke-Stage {
    param(
        [string]$Name,
        [string]$Spec
    )

    Write-Host ""
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host " Building: $Name" -ForegroundColor Cyan
    Write-Host " Spec:     $Spec" -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Cyan

    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    $args = @("--noconfirm")
    if ($Clean) { $args += "--clean" }
    $args += $Spec

    & pyinstaller @args
    if ($LASTEXITCODE -ne 0) {
        $sw.Stop()
        throw "PyInstaller failed for $Name after $([math]::Round($sw.Elapsed.TotalSeconds,1))s"
    }

    $sw.Stop()
    Write-Host ""
    Write-Host (" -> {0} completed in {1:N1}s" -f $Name, $sw.Elapsed.TotalSeconds) -ForegroundColor Green
}

$totalSw = [System.Diagnostics.Stopwatch]::StartNew()

if (-not $SkipDashboard) {
    if (Test-Path "EchoFlow.spec") {
        Invoke-Stage -Name "Dashboard shell" -Spec "EchoFlow.spec"
    } else {
        Write-Host " [skip] EchoFlow.spec not found in repo root." -ForegroundColor Yellow
    }
}

if (-not $SkipDaemon) {
    if (Test-Path "EchoFlow-Daemon.spec") {
        Invoke-Stage -Name "Daemon (full app)" -Spec "EchoFlow-Daemon.spec"
    } else {
        Write-Host " [skip] EchoFlow-Daemon.spec not found in repo root." -ForegroundColor Yellow
    }
}

$totalSw.Stop()

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host (" ALL BUILDS DONE in {0:N1}s" -f $totalSw.Elapsed.TotalSeconds) -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host " Next steps:"
Write-Host "   iscc installer\EchoFlow.iss"
Write-Host "   iscc installer\EchoFlow-Daemon.iss"
Write-Host ""
Write-Host " To sign artifacts before packaging the installers:"
Write-Host "   .\installer\sign.ps1 -PfxPath cert.pfx -PfxPassword <pwd>"
