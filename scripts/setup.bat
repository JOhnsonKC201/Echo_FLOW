@echo off
REM This script lives in scripts\ — set up the venv at the repo root.
cd /d "%~dp0.."
echo === Echo Flow — setup ===
where python >nul 2>&1
if errorlevel 1 (
    echo Python not found. Install Python 3.11+ from python.org and re-run.
    pause
    exit /b 1
)
python -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo === Done. ===
echo 1. Install Ollama from https://ollama.com  (optional, for local LLM cleanup)
echo 2. Run:  ollama pull qwen2.5:3b-instruct
echo 3. Start the app with run.bat
pause
