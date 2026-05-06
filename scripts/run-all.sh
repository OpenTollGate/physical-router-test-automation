#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

TIMESTAMP=$(date -u '+%Y%m%dT%H%M%SZ')
GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
RUN_ID="${TIMESTAMP}-${GIT_SHA}"
RAW_DIR="results/${RUN_ID}/raw"
mkdir -p "$RAW_DIR/api" "$RAW_DIR/phone"

VENV="${TOLLGATE_PYTHON_VENV:-$HOME/.tollgate-test-venv}"
source "$VENV/bin/activate"

RC=0

echo "=== Running Playwright LuCI tests ==="
"$SCRIPT_DIR/run-tests.sh" 2>&1 | tee "$RAW_DIR/playwright-output.log" || {
    echo "WARNING: Playwright tests failed (exit $?)"
    RC=1
}

echo ""
echo "=== Running pytest API tests ==="
pytest -m api \
    --html="$RAW_DIR/api/report.html" \
    --self-contained-html \
    --junitxml="$RAW_DIR/api/junit.xml" \
    -v --tb=short --timeout=60 --timeout-method=thread \
    2>&1 | tee "$RAW_DIR/api/output.log" || {
    echo "WARNING: API tests failed (exit $?)"
    RC=1
}

echo ""
echo "=== Running pytest phone tests ==="
pytest -m phone \
    --html="$RAW_DIR/phone/report.html" \
    --self-contained-html \
    --junitxml="$RAW_DIR/phone/junit.xml" \
    -v --tb=short --timeout=300 --timeout-method=thread \
    2>&1 | tee "$RAW_DIR/phone/output.log" || {
    echo "WARNING: Phone tests failed (exit $?)"
    RC=1
}

echo ""
echo "=== All test suites complete ==="
echo ""
echo "==> Results: ${RAW_DIR}/"
echo "==> Sanitize: ./scripts/sanitize-results.sh ${RAW_DIR} results/${RUN_ID}/sanitized"
echo "==> Publish:  ./scripts/publish-report.sh ${GIT_SHA} results/${RUN_ID}/sanitized"

exit $RC
