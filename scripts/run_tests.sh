#!/usr/bin/env bash
# Run the pytest suite on macOS or Linux. Mirrors scripts\run_tests.bat.
set -euo pipefail
cd "$(dirname "$0")/.."
# The test tooling lives in requirements-dev.txt, not the runtime requirements
# that setup.sh installs.
.venv/bin/python -m pip install -q -r requirements-dev.txt
exec .venv/bin/python -m pytest tests -q "$@"
