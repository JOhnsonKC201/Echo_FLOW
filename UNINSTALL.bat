@echo off
cd /d "%~dp0"
echo.
echo ============================================================
echo   Echo Flow  —  Uninstall
echo ============================================================
echo.

REM Kill any running daemon. The old WINDOWTITLE filter could never match:
REM run_silent.vbs starts the daemon hidden, so there is no window title to
REM filter on, and the for-loop below it had an empty body. The daemon stayed
REM alive and held .venv\python3xx.dll open, so the rmdir further down
REM partially failed while still printing [OK]. Same approach as RESTART.bat.
echo Stopping any running daemon...
taskkill /F /IM python.exe >nul 2>&1
taskkill /F /IM pythonw.exe >nul 2>&1
taskkill /F /IM wscript.exe >nul 2>&1
ping -n 3 127.0.0.1 >nul

REM Remove autostart shortcut
powershell -NoProfile -Command ^
  "$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'Echo Flow.lnk';" ^
  "if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Host '[OK] Autostart shortcut removed.' } else { Write-Host '[--] No autostart shortcut found.' }"

echo.
set /p KEEPVENV="Remove Python venv folder (.venv)? [y/N]: "
if /i "%KEEPVENV%"=="y" (
    rmdir /s /q .venv
    if exist ".venv" (
        echo [!!] venv could NOT be fully removed, a process may still hold it.
        echo      Close Echo Flow, then delete .venv by hand.
    ) else (
        echo [OK] venv removed.
    )
)

set /p KEEPDATA="Remove your dictation history (data/history.db)? [y/N]: "
if /i "%KEEPDATA%"=="y" (
    if exist "data\history.db" del /q "data\history.db"
    echo [OK] History removed.
)

set /p KEEPKEY="Remove GROQ_API_KEY from Windows env vars? [y/N]: "
if /i "%KEEPKEY%"=="y" (
    reg delete "HKCU\Environment" /v GROQ_API_KEY /f >nul 2>&1
    echo [OK] GROQ_API_KEY removed.
)

echo.
echo Done. You can now delete this folder if you want.
pause
