@echo off
cd /d "%~dp0"
call .venv\Scripts\activate
.venv\Scripts\python.exe -m pytest tests -v
pause
