#!/usr/bin/env bash
# Stop Echo Flow from starting at login on macOS: unloads and removes the
# LaunchAgent that scripts/install_autostart.sh created. The running daemon
# is stopped too; ./run.sh starts it by hand from then on.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -x .venv/bin/python ]; then
    exec .venv/bin/python -m src.launchagent uninstall
fi
exec python3 -m src.launchagent uninstall
