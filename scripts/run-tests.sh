#!/usr/bin/env bash
set -euo pipefail

# ── Arguments ───────────────────────────────────────────────────────────
TOLLGATE_COMMIT="${1:-}"
VIEWPORT="${2:-desktop}"
ROUTER_ID="${3:-${TOLLGATE_ROUTER_ID:-$(hostname -s 2>/dev/null || echo unknown)}}"

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

# ── Run directory ───────────────────────────────────────────────────────
TIMESTAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
RUN_DIR="$REPO_DIR/test-run-${TIMESTAMP}"
mkdir -p "$RUN_DIR"

echo "==> Run directory: $RUN_DIR"
echo "==> Tollgate commit: ${TOLLGATE_COMMIT:0:12}"
echo "==> Viewport: $VIEWPORT"
echo "==> Router: $ROUTER_ID ($TOLLGATE_ROUTER_IP)"

# ── Install dependencies if needed ──────────────────────────────────────
if [ ! -d "$REPO_DIR/node_modules" ]; then
  echo "==> Installing dependencies..."
  (cd "$REPO_DIR" && npm install)
fi

# ── Run Playwright ──────────────────────────────────────────────────────
cd "$TESTS_DIR"

echo ""
echo "==> Running Playwright tests..."
PLAYWRIGHT_JSON_OUTPUT_NAME="$RUN_DIR/results.json" \
  npx playwright test --config=playwright.config.mjs --reporter=html,json \
  || PW_EXIT=$?
PW_EXIT="${PW_EXIT:-0}"

echo ""
echo "==> Playwright finished (exit code: $PW_EXIT)"

# ── Move HTML report into run directory ─────────────────────────────────
# --reporter=html,json overrides config, discarding outputFolder.
# Playwright may output to tests/ or the repo root depending on resolution.
for report_src in "$TESTS_DIR/report" "$TESTS_DIR/playwright-report" "$REPO_DIR/playwright-report" "$REPO_DIR/report"; do
  if [ -d "$report_src" ]; then
    mv "$report_src" "$RUN_DIR/report"
    echo "==> HTML report moved from ${report_src##"$REPO_DIR"/}"
    break
  fi
done
if [ ! -d "$RUN_DIR/report" ]; then
  echo "WARNING: no HTML report directory found" >&2
fi

# ── Extract stats from JSON report ──────────────────────────────────────
PASSED=0
FAILED=0
FLAKY=0
SKIPPED=0
DURATION_MS=0

if [ -f "$RUN_DIR/results.json" ]; then
  read -r PASSED FAILED FLAKY SKIPPED DURATION_MS <<< "$(python3 - "$RUN_DIR/results.json" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1]))
s = d.get('stats', {})
print(f"{s.get('expected',0)} {s.get('unexpected',0)} {s.get('flaky',0)} {s.get('skipped',0)} {s.get('duration',0)}")
PYEOF
)"
  echo "==> Results: $PASSED passed, $FAILED failed, $FLAKY flaky, $SKIPPED skipped (${DURATION_MS}ms)"
else
  echo "WARNING: results.json not found — counts will be zero" >&2
fi

# ── Write run.json ─────────────────────────────────────────────────────
TEST_SUITE_COMMIT="$(git -C "$REPO_DIR" rev-parse HEAD)"
ISO_TIMESTAMP="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

python3 - "$TOLLGATE_COMMIT" "${TOLLGATE_BRANCH:-}" "${TOLLGATE_PR:-}" \
  "$TEST_SUITE_COMMIT" "$ROUTER_ID" "$TOLLGATE_ROUTER_IP" "$VIEWPORT" \
  "$ISO_TIMESTAMP" "$PASSED" "$FAILED" "$SKIPPED" "$FLAKY" "$DURATION_MS" \
  "$RUN_DIR/run.json" <<'PYEOF'
import json, sys
a = sys.argv[1:]
run = {
    'tollgate_commit': a[0],
    'tollgate_branch': a[1] if a[1] else None,
    'tollgate_pr': int(a[2]) if a[2] else None,
    'test_suite_commit': a[3],
    'test_type': 'e2e',
    'router_id': a[4],
    'router_ip': a[5],
    'viewport': a[6],
    'timestamp': a[7],
    'passed': int(a[8]),
    'failed': int(a[9]),
    'skipped': int(a[10]),
    'flaky': int(a[11]),
    'duration_ms': float(a[12]) if '.' in a[12] else int(a[12]),
}
with open(a[13], 'w') as f:
    json.dump(run, f, indent=2)
    f.write('\n')
PYEOF

echo "==> Wrote $RUN_DIR/run.json"

# ── Publish (if enabled) ────────────────────────────────────────────────
if [ "$TOLLGATE_PUBLISH" = "1" ] || [ "$TOLLGATE_PUBLISH" = "true" ]; then
  echo ""
  echo "==> Publishing report..."
  "$SCRIPT_DIR/publish-report.sh" "$RUN_DIR" || true
fi

# ── Summary ─────────────────────────────────────────────────────────────
echo ""
echo "====================================================================="
echo "  TEST SUMMARY"
echo "====================================================================="
echo "  Tollgate:   ${TOLLGATE_COMMIT:0:12}"
echo "  Router:     $ROUTER_ID ($TOLLGATE_ROUTER_IP)"
echo "  Viewport:   $VIEWPORT"
echo "  Passed:     $PASSED"
echo "  Failed:     $FAILED"
echo "  Flaky:      $FLAKY"
echo "  Skipped:    $SKIPPED"
DURATION_SEC="$(python3 -c "print(int(float('$DURATION_MS') / 1000))")"
echo "  Duration:   ${DURATION_SEC}s"
echo "  Run dir:    $RUN_DIR"
echo "====================================================================="

exit $PW_EXIT
