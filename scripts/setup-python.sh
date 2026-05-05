#!/usr/bin/env bash
set -euo pipefail
VENV="${TOLLGATE_PYTHON_VENV:-$HOME/.tollgate-test-venv}"
echo "Creating Python test venv at $VENV"
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q -r requirements.txt
echo "Done. Activate with: source $VENV/bin/activate"
