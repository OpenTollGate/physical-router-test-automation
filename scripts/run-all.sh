#!/usr/bin/env bash
set -euo pipefail

# ── Multi-runner orchestrator ───────────────────────────────────────────
# Runs Playwright LuCI, pytest API, and pytest phone test tiers.
# Creates ONE canonical run directory, delegates rendering to
# collect-results.py + render-report.py after all runners complete.
#
# Usage: ./scripts/run-all.sh [--test-plan PLAN]
# ────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

# ── Parse arguments ─────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    --test-plan) export TOLLGATE_TEST_PLAN="${2:-all}"; shift 2 ;;
    -h|--help)
      echo "Usage: ./scripts/run-all.sh [--test-plan PLAN]"
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# ── Activate Python venv ───────────────────────────────────────────────
VENV="${TOLLGATE_PYTHON_VENV:-$HOME/.tollgate-test-venv}"
source "$VENV/bin/activate"

# ── Generate run ID and directory ──────────────────────────────────────
TIMESTAMP=$(date -u '+%Y%m%dT%H%M%SZ')
GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
RUN_ID="${TIMESTAMP}-${GIT_SHA}"
RESULTS_DIR="$(pwd)/results/${RUN_ID}"

mkdir -p "$RESULTS_DIR/raw/api" "$RESULTS_DIR/raw/phone" "$RESULTS_DIR/raw/playwright" \
         "$RESULTS_DIR/report" "$RESULTS_DIR/artifacts/logs" "$RESULTS_DIR/artifacts/screenshots" \
         "$RESULTS_DIR/artifacts/traces"

# ── Export for child scripts ───────────────────────────────────────────
export TOLLGATE_RUN_ID="$RUN_ID"
export TOLLGATE_RESULTS_DIR="$RESULTS_DIR"

STARTED_AT=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

echo "==> Run ID:      $RUN_ID"
echo "==> Results dir: $RESULTS_DIR"
echo "==> Started at:  $STARTED_AT"
echo ""

# ── Run all test tiers (never abort on single failure) ─────────────────
RC=0

echo "=== Running Playwright LuCI tests ==="
"$SCRIPT_DIR/run-tests.sh" --no-render --run-dir "$RESULTS_DIR" || {
  echo "WARNING: Playwright tests failed (exit $?)"
  RC=1
}

echo ""
echo "=== Running pytest API tests ==="
"$SCRIPT_DIR/run-api.sh" --no-render --run-dir "$RESULTS_DIR" || {
  echo "WARNING: API tests failed (exit $?)"
  RC=1
}

echo ""
echo "=== Running pytest phone tests ==="
"$SCRIPT_DIR/run-phone.sh" --no-render --run-dir "$RESULTS_DIR" || {
  echo "WARNING: Phone tests failed (exit $?)"
  RC=1
}

# ── Record completion timestamp ────────────────────────────────────────
FINISHED_AT=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

# ── Collect results and render report ──────────────────────────────────
echo ""
echo "==> Collecting results..."

COLLECTOR_ARGS=(
  --run-dir "$RESULTS_DIR"
  --run-id "$RUN_ID"
  --sut-backend "${TOLLGATE_BACKEND:-go}"
  --sut-branch "${TOLLGATE_BRANCH:-}"
  --sut-pr "${TOLLGATE_PR:-}"
  --router-id "${TOLLGATE_ROUTER_ID:-}"
  --router-model "${TOLLGATE_ROUTER_MODEL:-}"
  --router-arch "${TOLLGATE_ROUTER_ARCH:-}"
  --client-type "${TOLLGATE_CLIENT_TYPE:-}"
  --viewport "${TOLLGATE_VIEWPORT:-desktop}"
  --test-plan "${TOLLGATE_TEST_PLAN:-all}"
  --started-at "$STARTED_AT"
  --finished-at "$FINISHED_AT"
  --allow-failures
)

# Add pytest runners only if their junit.xml exists
if [[ -f "$RESULTS_DIR/raw/api/junit.xml" ]]; then
  COLLECTOR_ARGS+=(--pytest "api=raw/api/junit.xml")
fi
if [[ -f "$RESULTS_DIR/raw/phone/junit.xml" ]]; then
  COLLECTOR_ARGS+=(--pytest "phone=raw/phone/junit.xml")
fi
# Add playwright runner if results.json exists
if [[ -f "$RESULTS_DIR/raw/playwright/results.json" ]]; then
  COLLECTOR_ARGS+=(--playwright "playwright=raw/playwright/results.json")
fi

python3 "$SCRIPT_DIR/collect-results.py" "${COLLECTOR_ARGS[@]}" || true
python3 "$SCRIPT_DIR/render-report.py" --run-dir "$RESULTS_DIR" || true

# ── Summary ────────────────────────────────────────────────────────────
echo ""
echo "====================================================================="
echo "  ALL TIERS COMPLETE"
echo "====================================================================="
echo "  Run ID:       $RUN_ID"
echo "  Started at:   $STARTED_AT"
echo "  Finished at:  $FINISHED_AT"
echo "  Results:      $RESULTS_DIR"
echo "  Report:       $RESULTS_DIR/report/index.html"
echo "  Overall:      $([ $RC -eq 0 ] && echo 'PASSED' || echo 'FAILED')"
echo "====================================================================="

exit $RC
