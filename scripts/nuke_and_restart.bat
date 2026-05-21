@echo off
cd /d "%~dp0"
echo.
echo ============================================================
echo   NUCLEAR RESTART — wipes ALL learning + restarts clean
echo ============================================================
echo.
echo This will:
echo   1. Kill all running instances
echo   2. Delete ALL dictation history (52 rows)
echo   3. Launch one fresh daemon
echo.
set /p OK="Type YES to proceed: "
if /i not "%OK%"=="YES" (
    echo Cancelled.
    pause
    exit /b
)

echo.
echo Step 1/4: Killing instances...
taskkill /F /IM python.exe >nul 2>&1
taskkill /F /IM pythonw.exe >nul 2>&1
taskkill /F /IM wscript.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo Step 2/4: Wiping history...
if exist "data\history.db" del /q "data\history.db"
if exist "data\history.html" del /q "data\history.html"

echo Step 3/4: Releasing ports...
timeout /t 2 /nobreak >nul

echo Step 4/4: Launching fresh daemon...
start "" wscript.exe "%CD%\run_silent.vbs"
timeout /t 4 /nobreak >nul

echo.
echo Done. Verifying:
tasklist /FI "IMAGENAME eq python.exe" 2>nul | findstr /I "python.exe"
echo.
echo You should see exactly ONE python.exe above.
echo Look at system tray for ONE green microphone.
echo Right-click tray then Language then click AUTO to be safe.
echo.
echo Then dictate "hi how you" — should give "Hi, how are you?"
echo.
pause
