@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo ============================================================
echo   Echo Flow  —  One-Click Installer
echo ============================================================
echo.

REM ---------- 1. Check Python ----------
where python >nul 2>&1
if errorlevel 1 (
    echo [X] Python is not installed.
    echo.
    echo Opening the Python download page now. Install Python 3.10 or newer.
    echo IMPORTANT: check the box "Add Python to PATH" during install.
    echo.
    start https://www.python.org/downloads/
    echo After installing Python, run this installer again.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
echo [OK] Python found: %PYVER%

REM ---------- 2. Groq API key (OPTIONAL — only used for cloud bootstrap) ----------
echo.
echo Echo Flow runs fully offline by default ^(local Whisper + Ollama^).
echo Groq is OPTIONAL and only used to bootstrap learning faster ^(first 50 dictations^).
echo Skip this step if you want a pure offline install.
echo.
for /f "tokens=2,*" %%A in ('reg query "HKCU\Environment" /v GROQ_API_KEY 2^>nul ^| findstr GROQ_API_KEY') do set "EXISTING_KEY=%%B"

if defined EXISTING_KEY (
    echo [OK] GROQ_API_KEY already set.
    goto :CONTINUE
)

set /p WANTGROQ="Add an optional Groq API key for faster bootstrap? [y/N]: "
if /i not "!WANTGROQ!"=="y" goto :CONTINUE

echo Sign up ^(free, no card^) at: https://console.groq.com/keys
start https://console.groq.com/keys
echo.
set /p GROQKEY="Paste your Groq API key (starts with gsk_), or press Enter to skip: "
if not "!GROQKEY!"=="" (
    setx GROQ_API_KEY "!GROQKEY!" >nul
    set "GROQ_API_KEY=!GROQKEY!"
    echo [OK] GROQ_API_KEY saved.
)

:CONTINUE
REM ---------- 3. Create venv + install deps ----------
echo.
echo Creating Python virtual environment...
if exist ".venv" (
    echo [OK] venv already exists.
) else (
    python -m venv .venv
    if errorlevel 1 (
        echo [X] Failed to create venv.
        pause
        exit /b 1
    )
    echo [OK] venv created.
)

echo Installing dependencies (this takes ~3-5 minutes the first time)...
call .venv\Scripts\activate
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
if errorlevel 1 (
    echo [X] Dependency install failed. See errors above.
    pause
    exit /b 1
)
echo [OK] Dependencies installed.

REM ---------- 3b. Install Ollama for offline cleanup ----------
echo.
echo Checking for Ollama (local LLM for offline grammar cleanup)...
where ollama >nul 2>&1
if errorlevel 1 (
    echo [!] Ollama not found.
    set /p WANTOLLAMA="Install Ollama now via winget? [Y/n]: "
    if /i not "!WANTOLLAMA!"=="n" (
        winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements
        if errorlevel 1 (
            echo [!] winget install failed. Download manually from https://ollama.com/download
        )
    )
) else (
    echo [OK] Ollama found.
)

where ollama >nul 2>&1
if not errorlevel 1 (
    echo Pulling qwen2.5:3b-instruct ^(~1.9 GB, CPU-friendly^)...
    ollama pull qwen2.5:3b-instruct
    if errorlevel 1 (
        echo [!] Model pull failed. Ollama may need a restart. Try: ollama pull qwen2.5:3b-instruct
    ) else (
        echo [OK] Ollama model ready.
    )
)

REM ---------- 4. Install autostart shortcut ----------
echo.
echo Installing autostart shortcut...
powershell -NoProfile -Command ^
  "$startup = [Environment]::GetFolderPath('Startup');" ^
  "$target = '%CD%\run_silent.vbs';" ^
  "$shortcut = Join-Path $startup 'Echo Flow.lnk';" ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$sc = $ws.CreateShortcut($shortcut);" ^
  "$sc.TargetPath = 'wscript.exe';" ^
  "$sc.Arguments = '\"' + $target + '\"';" ^
  "$sc.WorkingDirectory = '%CD%';" ^
  "$sc.IconLocation = 'shell32.dll,138';" ^
  "$sc.Description = 'Echo Flow - dictation daemon';" ^
  "$sc.Save();" ^
  "Write-Host '[OK] Autostart installed: ' $shortcut"

echo.
echo ============================================================
echo   Install complete!
echo ============================================================
echo.
echo Hotkey:  hold Ctrl + Shift, speak, release
echo.
echo Starting Echo Flow now (silent, in background)...
start "" wscript.exe "%CD%\run_silent.vbs"
echo.
echo From now on, it will auto-start every time you log in to Windows.
echo To stop it later: open Task Manager, end the python.exe process.
echo To uninstall: run UNINSTALL.bat
echo.
pause
