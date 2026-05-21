@echo off
cd /d "%~dp0"
echo.
echo ============================================================
echo   Echo Flow  -  Clean Restart
echo ============================================================
echo.
echo Step 1/3: Killing all running instances...
taskkill /F /IM python.exe >nul 2>&1
taskkill /F /IM pythonw.exe >nul 2>&1
taskkill /F /IM wscript.exe >nul 2>&1
echo   Done.
echo.
echo Step 2/3: Waiting 2 seconds for ports to release...
timeout /t 2 /nobreak >nul
echo   Done.
echo.
echo Step 3/3: Launching one fresh instance...
start "" wscript.exe "%CD%\run_silent.vbs"
timeout /t 3 /nobreak >nul
echo   Done.
echo.
echo Verifying running instances:
tasklist /FI "IMAGENAME eq python.exe" 2>nul | findstr /I "python.exe"
echo.
echo If you see exactly ONE python.exe above, you are good.
echo Look at your system tray (bottom-right) for ONE green microphone.
echo.
echo You can now close this window.
pause
