#!/usr/bin/env bash
# Start Echo Flow at login on macOS. Installs a per-user LaunchAgent that runs
# the daemon from this checkout and relaunches it if it ever crashes.
# Undo with scripts/uninstall_autostart.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
    echo "No virtualenv yet. Run scripts/setup.sh first." >&2
    exit 1
fi

.venv/bin/python -m src.launchagent install

cat <<'EOF'

launchd starts the daemon as the Python interpreter, not as Terminal, so
macOS will ask for Input Monitoring, Microphone and Accessibility again the
first time each fires; grant them to python. The daemon's console, including
the list of permissions still missing, is in logs/launchd.out.log.
EOF
