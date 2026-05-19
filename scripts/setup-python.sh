#!/usr/bin/env bash
set -euo pipefail

VENV="${TOLLGATE_PYTHON_VENV:-$HOME/.tollgate-test-venv}"

if [ -f "$VENV/bin/pytest" ]; then
  echo "==> Python venv already exists at $VENV"
  exit 0
fi

echo "==> Creating Python venv at $VENV"
python3 -m venv "$VENV"
"$VENV/bin/pip" install -r requirements.txt
echo "==> Done. Activate: source $VENV/bin/activate"
