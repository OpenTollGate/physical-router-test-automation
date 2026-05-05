#!/usr/bin/env bash
set -euo pipefail

VIEWPORT="${1:-desktop}"
export TOLLGATE_VIEWPORT="$VIEWPORT"

export TOLLGATE_LUCI_URL="${TOLLGATE_LUCI_URL:-http://192.168.13.112:8080}"
export TOLLGATE_LUCI_USER="${TOLLGATE_LUCI_USER:-root}"
export TOLLGATE_LUCI_PASSWORD="${TOLLGATE_LUCI_PASSWORD:-}"

if [ -z "$TOLLGATE_LUCI_PASSWORD" ]; then
  echo "ERROR: TOLLGATE_LUCI_PASSWORD env var is required" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/../tests"

if [ ! -d "../node_modules" ]; then
  echo "==> Installing dependencies..."
  cd ..
  npm install
  cd tests
fi

echo "==> Running Playwright tests (viewport: $VIEWPORT)..."
npx playwright test --config=playwright.config.mjs
EXIT_CODE=$?

echo ""
if [ $EXIT_CODE -eq 0 ]; then
  echo "==> All tests passed."
else
  echo "==> Some tests FAILED (exit code: $EXIT_CODE)."
fi
exit $EXIT_CODE
