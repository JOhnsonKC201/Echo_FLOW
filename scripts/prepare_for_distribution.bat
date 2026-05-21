@echo off
REM ================================================================
REM  Echo Flow — prepare a clean copy for distribution.
REM
REM  Creates C:\echo flow dist\ with only the files a recipient
REM  needs. Strips: your dictation history, logs, caches, venv,
REM  and any generated HTML viewers.
REM
REM  After this runs, ZIP C:\echo flow dist\ and send it.
REM ================================================================
setlocal EnableDelayedExpansion

REM Source = the folder containing scripts/, so .. from here.
set "SRC=%~dp0.."
pushd "%SRC%"
set "SRC=%CD%"
popd
set "DIST=C:\echo flow dist"

echo.
echo === Echo Flow distribution prep ===
echo Source: %SRC%
echo Target: %DIST%
echo.

if exist "%DIST%" (
    echo [!] %DIST% already exists.
    set /p OVERWRITE="Delete and recreate? [y/N]: "
    if /i not "!OVERWRITE!"=="y" (
        echo Aborted.
        pause
        exit /b 1
    )
    rmdir /s /q "%DIST%"
)

echo Step 1/3: Copying files (using robocopy to skip junk)...
robocopy "%SRC%" "%DIST%" /E ^
    /XD .venv .pytest_cache __pycache__ data logs ^
    /XF *.pyc *.log watchdog.pid history.db ruvector.db graph.html history.html ^
    /NFL /NDL /NJH /NJS /NC /NS >nul

REM Recreate empty data/ and logs/ so the daemon has its folders.
mkdir "%DIST%\data" 2>nul
mkdir "%DIST%\logs" 2>nul

REM Drop a .gitkeep so the empty folders survive zipping.
echo. > "%DIST%\data\.gitkeep"
echo. > "%DIST%\logs\.gitkeep"

echo Step 2/3: Cleaning any __pycache__ that snuck in...
for /d /r "%DIST%" %%d in (__pycache__) do (
    if exist "%%d" rmdir /s /q "%%d"
)

echo Step 3/3: Verifying...
echo.
echo Files in distribution:
dir /b "%DIST%"
echo.
echo Size:
powershell -NoProfile -Command "$s = (Get-ChildItem -Recurse '%DIST%' | Measure-Object Length -Sum).Sum; '   {0:N1} MB' -f ($s / 1MB)"

echo.
echo ================================================================
echo  Ready. Your friend needs to:
echo    1. Unzip the folder anywhere
echo    2. Double-click INSTALL.bat (creates venv, installs deps)
echo    3. Speak — hold Ctrl+Shift to dictate
echo.
echo  No personal data inside this copy.
echo ================================================================
echo.
pause
