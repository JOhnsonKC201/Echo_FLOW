#!/usr/bin/env bash
# Open the Echo Flow dashboard window on macOS or Linux. Mirrors run_dashboard.bat.
#
# The daemon must already be running (./run.sh). This only opens the window,
# which connects to the daemon's local server at the port in data/dashboard.port.
# Without pywebview installed it falls back to your default browser.
set -euo pipefail
cd "$(dirname "$0")"

if [ -x .venv/bin/python ]; then
    exec .venv/bin/python -m src.dashboard.window "$@"
fi
exec python3 -m src.dashboard.window "$@"
