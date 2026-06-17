@echo off
REM This script lives in scripts\ — operate from the repo root, where .venv is.
cd /d "%~dp0.."
call .venv\Scripts\activate
REM Ensure the test tooling is present (it lives in requirements-dev.txt, not the
REM runtime requirements that setup.bat installs).
.venv\Scripts\python.exe -m pip install -q -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests -q
pause
