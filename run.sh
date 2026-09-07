#!/usr/bin/env bash
# Start the Echo Flow daemon on macOS or Linux. Mirrors run.bat.
#
# Runs in the foreground so the console shows what the daemon is doing; the
# tray icon appears in the menu bar. Quit from the tray menu or with Ctrl+C.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "No virtualenv yet. Running scripts/setup.sh first..."
    scripts/setup.sh
fi

if [ -z "${GROQ_API_KEY:-}" ]; then
    echo "[info] GROQ_API_KEY not set. Regular dictation is local anyway; only the"
    echo "       optional Prompt-Engineering mode falls back to the local model."
fi

exec .venv/bin/python -m src.main "$@"
