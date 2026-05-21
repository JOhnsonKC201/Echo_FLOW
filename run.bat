@echo off
cd /d "%~dp0"

REM Pull GROQ_API_KEY from User registry if not in current env (handles stale shells)
if "%GROQ_API_KEY%"=="" (
    for /f "tokens=2,*" %%A in ('reg query "HKCU\Environment" /v GROQ_API_KEY 2^>nul ^| findstr GROQ_API_KEY') do set "GROQ_API_KEY=%%B"
)

if not exist ".venv" (
    echo Creating virtualenv...
    python -m venv .venv
    call .venv\Scripts\activate
    pip install --upgrade pip
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate
)

if "%GROQ_API_KEY%"=="" (
    echo [WARN] GROQ_API_KEY not set — will use local fallback only.
) else (
    echo [OK] GROQ_API_KEY loaded.
)

python -m src.main
REM Only pause when run from an interactive console window (not from .vbs).
REM SESSIONNAME=Console when interactive; pause only if not flagged silent.
if not "%WISPR_SILENT%"=="1" pause
