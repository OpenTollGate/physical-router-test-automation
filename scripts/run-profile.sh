#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="${TOLLGATE_PYTHON_VENV:-$HOME/.tollgate-test-venv}"
if [[ ! -f "$VENV/bin/activate" ]]; then
  echo "ERROR: Python venv not found at $VENV" >&2
  echo "Run: ./scripts/setup-python.sh" >&2
  exit 1
fi
source "$VENV/bin/activate"
exec python3 "$SCRIPT_DIR/run-profile.py" "$@"
