#!/usr/bin/env bash
# One-time setup for macOS (and Linux): create the venv at the repo root and
# install the runtime dependencies. Mirrors scripts\setup.bat.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 not found. Install Python 3.11+ (python.org or Homebrew) and re-run." >&2
    exit 1
fi

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

cat <<'EOF'

Setup complete. Start Echo Flow with:  ./run.sh

macOS asks for two permissions the first time the hotkey and paste fire.
Grant them to the app that runs Echo Flow (Terminal, iTerm, or python) in
System Settings > Privacy & Security:
  - Input Monitoring   (so the push-to-talk hotkey is seen)
  - Accessibility      (so the finished text can be pasted at your cursor)
EOF
