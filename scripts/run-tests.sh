#!/usr/bin/env bash
set -euo pipefail

# ── Parse flags and positional args ──────────────────────────────────────
NO_RENDER=false
RUN_DIR_ARG=""
POSITIONAL=()

while [[ $# -gt 0 ]]; do
  case $1 in
    --no-render) NO_RENDER=true; shift ;;
    --run-dir)   RUN_DIR_ARG="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: ./scripts/run-tests.sh [--no-render] [--run-dir DIR] [tollgate-commit] [desktop|mobile] [router-id]"
      exit 0
      ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done

TOLLGATE_COMMIT="${POSITIONAL[0]:-}"
VIEWPORT="${POSITIONAL[1]:-desktop}"
ROUTER_ID="${POSITIONAL[2]:-${TOLLGATE_ROUTER_ID:-$(hostname -s 2>/dev/null || echo unknown)}}"

# ── Environment defaults ────────────────────────────────────────────────
export TOLLGATE_LUCI_URL="${TOLLGATE_LUCI_URL:-http://192.168.13.112:8080}"
export TOLLGATE_LUCI_USER="${TOLLGATE_LUCI_USER:-root}"
export TOLLGATE_LUCI_PASSWORD="${TOLLGATE_LUCI_PASSWORD:-}"
export TOLLGATE_VIEWPORT="$VIEWPORT"
export TOLLGATE_SSH_HOST="${TOLLGATE_SSH_HOST:-}"
export TOLLGATE_PUBLISH="${TOLLGATE_PUBLISH:-}"
export TOLLGATE_BRANCH="${TOLLGATE_BRANCH:-}"
export TOLLGATE_PR="${TOLLGATE_PR:-}"
export TOLLGATE_BACKEND="${TOLLGATE_BACKEND:-go}"

# Derive router IP from LUCI URL (strip scheme and port)
TOLLGATE_ROUTER_IP="${TOLLGATE_ROUTER_IP:-$(echo "$TOLLGATE_LUCI_URL" | sed -E 's#^https?://##;s#:[0-9]+.*##')}"

if [ -z "$TOLLGATE_LUCI_PASSWORD" ]; then
  echo "ERROR: TOLLGATE_LUCI_PASSWORD env var is required" >&2
  exit 1
fi

# ── Paths ───────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TESTS_DIR="$REPO_DIR/tests"

# ── Pre-flight connectivity check ───────────────────────────────────────
echo "==> Checking router connectivity..."
ROUTER_PASSWORD="$TOLLGATE_LUCI_PASSWORD"
export SSHPASS="$ROUTER_PASSWORD"
if ! sshpass -e ssh -O -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 \
  "root@${TOLLGATE_ROUTER_IP}" 'echo ok' &>/dev/null; then
  echo "ERROR: Cannot reach router at ${TOLLGATE_ROUTER_IP}" >&2
  echo "  - Is the router powered on?" >&2
  echo "  - Is SSH enabled?" >&2
  echo "  - Is TOLLGATE_LUCI_URL correct?" >&2
  exit 1
fi
echo "==> Router reachable at ${TOLLGATE_ROUTER_IP}"

# Resolve commit hash from branch if not provided
if [ -z "$TOLLGATE_COMMIT" ]; then
  if [ -n "$TOLLGATE_BRANCH" ]; then
    echo "==> Resolving commit from branch ${TOLLGATE_BRANCH}..."
    TOLLGATE_COMMIT=$(git -C "$REPO_DIR" rev-parse "origin/${TOLLGATE_BRANCH}" 2>/dev/null || echo "unknown")
  else
    TOLLGATE_COMMIT=$(git -C "$REPO_DIR" rev-parse HEAD 2>/dev/null || echo "unknown")
  fi
fi

# ── Run ID and results directory ────────────────────────────────────────
if [[ -n "$RUN_DIR_ARG" ]]; then
  RESULTS_DIR="$(cd "$(dirname "$RUN_DIR_ARG")" && pwd)/$(basename "$RUN_DIR_ARG")"
  RUN_ID="$(basename "$RESULTS_DIR")"
elif [[ -n "${TOLLGATE_RESULTS_DIR:-}" ]]; then
  RESULTS_DIR="$TOLLGATE_RESULTS_DIR"
  RUN_ID="${TOLLGATE_RUN_ID:-$(basename "$RESULTS_DIR")}"
else
  TIMESTAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
  GIT_SHA="$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
  RUN_ID="${TOLLGATE_RUN_ID:-${TIMESTAMP}-${GIT_SHA}}"
  RESULTS_DIR="$REPO_DIR/results/$RUN_ID"
fi

export TOLLGATE_RUN_ID="$RUN_ID"
export TOLLGATE_RESULTS_DIR="$RESULTS_DIR"

# ── Directory structure ─────────────────────────────────────────────────
mkdir -p "$RESULTS_DIR/raw/playwright" "$RESULTS_DIR/report" "$RESULTS_DIR/artifacts"

STARTED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

echo "==> Run ID:      $RUN_ID"
echo "==> Results dir: $RESULTS_DIR"
echo "==> Tollgate:    ${TOLLGATE_COMMIT:0:12}"
echo "==> Viewport:    $VIEWPORT"
echo "==> Router:      $ROUTER_ID ($TOLLGATE_ROUTER_IP)"

# ── Install dependencies if needed ──────────────────────────────────────
if [ ! -d "$REPO_DIR/node_modules" ]; then
  echo "==> Installing dependencies..."
  (cd "$REPO_DIR" && npm install)
fi

# ── Run Playwright ──────────────────────────────────────────────────────
cd "$TESTS_DIR"

echo ""
echo "==> Running Playwright tests..."
PW_EXIT=0
PLAYWRIGHT_JSON_OUTPUT_NAME="$RESULTS_DIR/raw/playwright/results.json" \
  npx playwright test --config=playwright.config.mjs \
    --reporter=html,json \
    --output="$RESULTS_DIR/raw/playwright" \
    2>&1 | tee "$RESULTS_DIR/raw/playwright/output.log" || PW_EXIT=$?

echo ""
echo "==> Playwright finished (exit code: $PW_EXIT)"

FINISHED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# ── Move HTML report into run directory ─────────────────────────────────
# --reporter=html,json overrides config, discarding outputFolder.
# Playwright may output to tests/ or the repo root depending on resolution.
for report_src in \
  "$TESTS_DIR/report" \
  "$TESTS_DIR/playwright-report" \
  "$REPO_DIR/playwright-report" \
  "$REPO_DIR/report" \
  "$RESULTS_DIR/raw/playwright/report"; do
  if [ -d "$report_src" ] && [ "$report_src" != "$RESULTS_DIR/raw/playwright/report" ]; then
    mv "$report_src" "$RESULTS_DIR/raw/playwright/report"
    echo "==> HTML report moved from ${report_src##"$REPO_DIR"/}"
    break
  fi
done
if [ ! -d "$RESULTS_DIR/raw/playwright/report" ]; then
  echo "WARNING: no HTML report directory found" >&2
fi

# ── Collect and render ──────────────────────────────────────────────────
if [[ "$NO_RENDER" == "false" ]]; then
  echo "==> Collecting results..."
  python3 "$SCRIPT_DIR/collect-results.py" \
    --run-dir "$RESULTS_DIR" \
    --playwright "playwright=raw/playwright/results.json" \
    --run-id "$RUN_ID" \
    --sut-commit "$TOLLGATE_COMMIT" \
    --sut-branch "${TOLLGATE_BRANCH:-}" \
    --sut-pr "${TOLLGATE_PR:-}" \
    --sut-backend "${TOLLGATE_BACKEND:-go}" \
    --router-id "$ROUTER_ID" \
    --router-model "${TOLLGATE_ROUTER_MODEL:-}" \
    --router-arch "${TOLLGATE_ROUTER_ARCH:-}" \
    --client-type "${TOLLGATE_CLIENT_TYPE:-}" \
    --viewport "$VIEWPORT" \
    --test-plan "${TOLLGATE_TEST_PLAN:-playwright-luci}" \
    --started-at "$STARTED_AT" \
    --finished-at "$FINISHED_AT" \
    --allow-failures

  echo "==> Rendering report..."
  python3 "$SCRIPT_DIR/render-report.py" --run-dir "$RESULTS_DIR"
fi

# ── Publish (if enabled) ────────────────────────────────────────────────
if [ "$TOLLGATE_PUBLISH" = "1" ] || [ "$TOLLGATE_PUBLISH" = "true" ]; then
  echo ""
  echo "==> Publishing report..."
  "$SCRIPT_DIR/publish-report.sh" "$RESULTS_DIR" || true
fi

# ── Summary ─────────────────────────────────────────────────────────────
# Read counts from summary.json if available, else show raw Playwright exit
if [[ -f "$RESULTS_DIR/summary.json" ]]; then
  read -r PASSED FAILED SKIPPED FLAKY DURATION_MS <<< "$(python3 - "$RESULTS_DIR/summary.json" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
c = d.get("counts", {})
dur = sum(r.get("duration_ms", 0) for r in d.get("runners", [{}]))
print(f"{c.get('passed',0)} {c.get('failed',0)} {c.get('skipped',0)} {c.get('flaky',0)} {dur}")
PYEOF
)"
else
  PASSED="?" FAILED="?" SKIPPED="?" FLAKY="?" DURATION_MS="0"
fi

echo ""
echo "====================================================================="
echo "  TEST SUMMARY"
echo "====================================================================="
echo "  Run ID:     $RUN_ID"
echo "  Tollgate:   ${TOLLGATE_COMMIT:0:12}"
echo "  Router:     $ROUTER_ID ($TOLLGATE_ROUTER_IP)"
echo "  Viewport:   $VIEWPORT"
echo "  Passed:     $PASSED"
echo "  Failed:     $FAILED"
echo "  Flaky:      $FLAKY"
echo "  Skipped:    $SKIPPED"
if [[ "$DURATION_MS" != "?" && "$DURATION_MS" != "0" ]]; then
  DURATION_SEC="$(python3 -c "print(int(float('$DURATION_MS') / 1000))")"
  echo "  Duration:   ${DURATION_SEC}s"
fi
echo "  Results:    $RESULTS_DIR"
echo "====================================================================="

exit $PW_EXIT
