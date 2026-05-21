@echo off
cd /d "%~dp0"
echo.
echo ============================================================
echo   Echo Flow  —  Uninstall
echo ============================================================
echo.

REM Kill any running daemon
echo Stopping any running daemon...
taskkill /F /IM wscript.exe /FI "WINDOWTITLE eq run_silent*" >nul 2>&1
for /f "tokens=2" %%P in ('tasklist /FI "IMAGENAME eq python.exe" /FO csv ^| findstr /I "python.exe"') do (
    REM Best-effort — only kills python.exe processes, user can confirm manually
)

REM Remove autostart shortcut
powershell -NoProfile -Command ^
  "$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'Echo Flow.lnk';" ^
  "if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Host '[OK] Autostart shortcut removed.' } else { Write-Host '[--] No autostart shortcut found.' }"

echo.
set /p KEEPVENV="Remove Python venv folder (.venv)? [y/N]: "
if /i "%KEEPVENV%"=="y" (
    rmdir /s /q .venv
    echo [OK] venv removed.
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
