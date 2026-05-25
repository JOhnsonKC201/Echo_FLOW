@echo off
REM Launches the Echo Flow desktop dashboard as a native window.
REM
REM The daemon must already be running (run.bat / run_silent.vbs) — this
REM script only opens the window. The window connects to the daemon's
REM Flask server at the port written to data\dashboard.port.

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m src.dashboard.window %*
) else (
    python -m src.dashboard.window %*
)
