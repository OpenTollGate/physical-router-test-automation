#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

echo "=== Running Playwright LuCI tests ==="
"$SCRIPT_DIR/run-tests.sh" || echo "WARNING: Playwright tests failed (exit $?)"

echo ""
echo "=== Running pytest API tests ==="
"$SCRIPT_DIR/run-api.sh" || echo "WARNING: API tests failed (exit $?)"

echo ""
echo "=== Running pytest phone tests ==="
"$SCRIPT_DIR/run-phone.sh" || echo "WARNING: Phone tests failed (exit $?)"

echo ""
echo "=== All test suites complete ==="
