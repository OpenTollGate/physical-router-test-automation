#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."
VENV="${TOLLGATE_PYTHON_VENV:-$HOME/.tollgate-test-venv}"
source "$VENV/bin/activate"
pytest -m phone "$@"
