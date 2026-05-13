#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

VENV="${TOLLGATE_PYTHON_VENV:-$HOME/.tollgate-test-venv}"
source "$VENV/bin/activate"

TIMESTAMP=$(date -u '+%Y%m%dT%H%M%SZ')
GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
RUN_ID="${TIMESTAMP}-${GIT_SHA}"
RAW_DIR="results/${RUN_ID}/raw"
mkdir -p "$RAW_DIR"

pytest -m phone \
    --publish \
    --html="$RAW_DIR/report.html" \
    --self-contained-html \
    --junitxml="$RAW_DIR/junit.xml" \
    -v --tb=short --timeout=300 --timeout-method=thread \
    "$@" 2>&1 | tee "$RAW_DIR/output.log"

echo ""
echo "==> Results: ${RAW_DIR}/"
echo "==> Sanitize: ./scripts/sanitize-results.sh ${RAW_DIR} results/${RUN_ID}/sanitized"
echo "==> Publish:  ./scripts/publish-report.sh ${GIT_SHA} results/${RUN_ID}/sanitized"
