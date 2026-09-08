#!/usr/bin/env bash
# Kill and relaunch the Echo Flow daemon on macOS or Linux. Mirrors RESTART.bat.
#
# The daemon loads code and config once at startup, so run this after editing
# config.yaml or pulling new code. When autostart is installed, launchd does
# the restart; otherwise the running daemon is stopped by its PID file and
# ./run.sh starts a fresh one in the foreground.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "No virtualenv yet. Run scripts/setup.sh first." >&2
    exit 1
fi

if .venv/bin/python -m src.launchagent restart; then
    exit 0
fi

if [ -f data/wispr.pid ]; then
    pid="$(cat data/wispr.pid)"
    if kill -0 "$pid" 2>/dev/null; then
        echo "Stopping Echo Flow (pid $pid)..."
        kill "$pid"
        for _ in $(seq 1 20); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.5
        done
        kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
    fi
fi

echo "Starting a fresh Echo Flow..."
exec ./run.sh
