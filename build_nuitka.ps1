# build_nuitka.ps1 — Compile Echo Flow to a native Windows binary via Nuitka.
#
# Why Nuitka (vs PyInstaller)?
#   Windows 11 Smart App Control (SAC) learns the PyInstaller bootloader
#   signature and blocks unsigned rebuilds. Nuitka emits a real C binary with
#   no shared bootloader fingerprint, so SAC has no prior to flag.
#
# Prerequisites (one-time):
#   .venv\Scripts\python.exe -m pip install nuitka zstandard ordered-set
#   # A C compiler is required. Nuitka will download a bundled MinGW64+clang
#   # the first time you pass --mingw64 (recommended on Windows).
#
# Usage:
#   .\build_nuitka.ps1
#
# Output:
#   dist_nuitka\app.dist\EchoFlow.exe   (standalone folder, ~60-90MB)
#
# Build time: 5-10 minutes cold, ~2 minutes warm (Nuitka caches C objects).

$ErrorActionPreference = "Stop"

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Virtualenv not found at $python. Activate or create .venv first."
    exit 1
}

Write-Host "[nuitka] Compiling app.py -> dist_nuitka\ ..." -ForegroundColor Cyan

& $python -m nuitka `
    --standalone `
    --mingw64 `
    --windows-console-mode=disable `
    --windows-icon-from-ico=assets/icon.ico `
    --output-filename=EchoFlow.exe `
    --output-dir=dist_nuitka `
    --company-name="Echo Flow" `
    --product-name="Echo Flow" `
    --file-version=0.1.0.0 `
    --product-version=0.1.0.0 `
    --file-description="Echo Flow desktop assistant" `
    --copyright="Copyright (c) 2026 Echo Flow" `
    --assume-yes-for-downloads `
    --include-data-dir=src/dashboard/templates=src/dashboard/templates `
    --include-data-dir=src/dashboard/static=src/dashboard/static `
    --include-data-files=config.yaml=config.yaml `
    --include-data-files=assets/icon.ico=assets/icon.ico `
    --include-data-files=assets/icon.png=assets/icon.png `
    --include-package=webview `
    --include-package=pystray `
    --include-package=pynput `
    --include-package=flask `
    --include-package=jinja2 `
    --include-module=webview.platforms.winforms `
    --include-module=pystray._win32 `
    --include-module=pynput.keyboard._win32 `
    --include-module=pynput.mouse._win32 `
    --include-module=jinja2.ext `
    --nofollow-import-to=torch `
    --nofollow-import-to=transformers `
    --nofollow-import-to=sentence_transformers `
    --nofollow-import-to=faster_whisper `
    --nofollow-import-to=silero_vad `
    --nofollow-import-to=sounddevice `
    --nofollow-import-to=matplotlib `
    --nofollow-import-to=pandas `
    --nofollow-import-to=numpy.tests `
    --nofollow-import-to=tkinter `
    --nofollow-import-to=unittest `
    app.py

if ($LASTEXITCODE -ne 0) {
    Write-Error "[nuitka] Build failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-Host "[nuitka] Done. Binary at: dist_nuitka\app.dist\EchoFlow.exe" -ForegroundColor Green
Write-Host "[nuitka] Smoke test: .\dist_nuitka\app.dist\EchoFlow.exe" -ForegroundColor Yellow
