#!/usr/bin/env bash
set -euo pipefail

# ── Unified PR testing workflow ────────────────────────────────────────────
# Deploy a PR branch to router, run API tests, generate reports.
#
# Usage:
#   ./scripts/test-pr.sh --pr <N> [--reset] [--test api|all] [--publish] [--router ID]
#   ./scripts/test-pr.sh --branch <NAME> [--reset] [--test api|all] [--publish] [--router ID]
#
# Required environment variables:
#   TOLLGATE_LUCI_PASSWORD — Router SSH and LuCI password
#
# Optional environment variables:
#   ROUTER_IP, TOLLGATE_SSH_HOST — Router IP for SSH
#   TOLLGATE_SSH_KEY — SSH key path (default: ~/.ssh/id_ed25519)
#   TOLLGATE_ROUTER_ID — Router ID (default: from .env)
# ───────────────────────────────────────────────────────────────────────────

# ── Parse CLI args ───────────────────────────────────────────────────────

PR_NUM=""
BRANCH=""
RESET=false
TEST_TYPE="api"
PUBLISH=false
ROUTER_ID=""
ARTIFACT_REPO=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --pr)
      PR_NUM="$2"
      shift 2
      ;;
    --branch)
      BRANCH="$2"
      shift 2
      ;;
    --reset)
      RESET=true
      shift
      ;;
    --test)
      TEST_TYPE="$2"
      if [[ "$TEST_TYPE" != "api" && "$TEST_TYPE" != "all" ]]; then
        echo "ERROR: --test must be 'api' or 'all'" >&2
        exit 1
      fi
      shift 2
      ;;
    --publish)
      PUBLISH=true
      shift
      ;;
    --repo)
      ARTIFACT_REPO="$2"
      shift 2
      ;;
    --router)
      ROUTER_ID="$2"
      shift 2
      ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      echo "Usage: $0 --pr <N> | --branch <NAME> [--reset] [--test api|all] [--publish] [--repo REPO] [--router ID]" >&2
      exit 1
      ;;
  esac
done

# ── Validate required args ───────────────────────────────────────────────
if [[ -z "$PR_NUM" && -z "$BRANCH" ]]; then
  echo "ERROR: Either --pr or --branch is required" >&2
  echo "Usage: $0 --pr <N> | --branch <NAME> [--reset] [--test api|all] [--publish] [--router ID]" >&2
  exit 1
fi

# ── Load environment variables ───────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ ! -f "$REPO_DIR/.env" ]]; then
  echo "ERROR: .env file not found at $REPO_DIR/.env" >&2
  exit 1
fi

set -a
source "$REPO_DIR/.env"
set +a

# ── Set defaults ────────────────────────────────────────────────────────
ROUTER_IP="${ROUTER_IP:-${TOLLGATE_SSH_HOST:-}}"
ROUTER_ID="${ROUTER_ID:-${TOLLGATE_ROUTER_ID:-}}"
ROUTER_SSH_KEY="${TOLLGATE_SSH_KEY:-~/.ssh/id_ed25519}"

# Auto-resolve arch from router inventory if not set
if [[ -z "${TOLLGATE_ROUTER_ARCH:-}" && -n "$ROUTER_ID" && -f "$REPO_DIR/config/routers.json" ]]; then
  TOLLGATE_ROUTER_ARCH=$(python3 -c "
import json, sys
inv = json.load(open('$REPO_DIR/config/routers.json'))
print(inv.get('routers', {}).get('$ROUTER_ID', {}).get('arch', ''))
" 2>/dev/null)
fi

# Check for password
if [[ -z "$TOLLGATE_LUCI_PASSWORD" && -z "$TOLLGATE_SSH_PASSWORD" ]]; then
  echo "ERROR: TOLLGATE_LUCI_PASSWORD or TOLLGATE_SSH_PASSWORD is required" >&2
  exit 1
fi

# Resolve router ID to IP if not provided
if [[ -z "$ROUTER_IP" && -n "$ROUTER_ID" ]]; then
  echo "ERROR: ROUTER_IP or TOLLGATE_SSH_HOST is required when ROUTER_ID is set" >&2
  exit 1
fi

# ── Resolve PR to branch + SHA ───────────────────────────────────────────

if [[ -n "$PR_NUM" ]]; then
  echo "==> Resolving PR $PR_NUM..."
  BRANCH=$(gh pr view "$PR_NUM" --repo OpenTollGate/tollgate-module-basic-go --json headRefName --jq '.headRefName')
  COMMIT_SHA=$(gh pr view "$PR_NUM" --repo OpenTollGate/tollgate-module-basic-go --json headRefOid --jq '.headRefOid')
  FORK_REPO=$(gh pr view "$PR_NUM" --repo OpenTollGate/tollgate-module-basic-go --json headRepository --jq '.headRepository.owner.login + "/" + .headRepository.name' 2>/dev/null || echo "")
  TOLLGATE_COMMIT="${COMMIT_SHA:0:12}"
  TOLLGATE_PR="$PR_NUM"
  TOLLGATE_BRANCH="$BRANCH"
  if [[ -n "$FORK_REPO" && -z "$ARTIFACT_REPO" ]]; then
    ARTIFACT_REPO="$FORK_REPO"
  fi
else
  TOLLGATE_BRANCH="$BRANCH"
  LOOKUP_REPO="${ARTIFACT_REPO:-OpenTollGate/tollgate-module-basic-go}"
  COMMIT_SHA=$(gh api "repos/${LOOKUP_REPO}/commits/${BRANCH}" --jq '.sha' 2>/dev/null || echo "unknown")
  TOLLGATE_COMMIT="${COMMIT_SHA:0:12}"
fi

echo "==> Tollgate branch: $TOLLGATE_BRANCH"
echo "==> Tollgate commit: ${TOLLGATE_COMMIT:0:12}"
if [[ -n "$ARTIFACT_REPO" ]]; then
  echo "==> Artifact repo: $ARTIFACT_REPO"
fi

# ── Pre-flight connectivity check ───────────────────────────────────────
echo "==> Checking router connectivity..."
SSH_CHECK_KEY="${TOLLGATE_SSH_KEY:-}"
_check_ssh() {
  if [[ -n "$SSH_CHECK_KEY" ]]; then
    ssh -i "$SSH_CHECK_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=5 -o LogLevel=ERROR "root@${ROUTER_IP}" 'echo ok' &>/dev/null
  else
    export SSHPASS="${TOLLGATE_SSH_PASSWORD:-$TOLLGATE_LUCI_PASSWORD}"
    sshpass -e ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=5 -o LogLevel=ERROR "root@${ROUTER_IP}" 'echo ok' &>/dev/null
  fi
}
if _check_ssh; then
  echo "==> Router reachable at ${ROUTER_IP}"
else
  if [[ "$RESET" == "true" ]]; then
    echo "WARNING: Router not reachable yet — firstboot_reset will wait for it" >&2
  else
    echo "ERROR: Cannot reach router at ${ROUTER_IP}" >&2
    echo "  - Is the router powered on?" >&2
    echo "  - Is SSH enabled?" >&2
    exit 1
  fi
fi

# ── Run directory ───────────────────────────────────────────────────────
TIMESTAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
RUN_DIR="$REPO_DIR/results/pr-test-$TIMESTAMP"
mkdir -p "$RUN_DIR/raw"
mkdir -p "$RUN_DIR/report"

echo "==> Run directory: $RUN_DIR"
echo "==> Tollgate commit: ${TOLLGATE_COMMIT:0:12}"
echo "==> Viewport: ${TOLLGATE_VIEWPORT:-desktop}"
echo "==> Router: ${ROUTER_ID:-$ROUTER_IP} ($ROUTER_IP)"

# ── Factory reset (if requested) ─────────────────────────────────────────
if [[ "$RESET" == "true" ]]; then
  echo "==> Factory resetting router (firstboot)..."
  python3 -c "
import os, sys, logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')
from lib.router import Router
from lib.deploy import firstboot_reset
r = Router(host='${ROUTER_IP}', phone_ip='', phone_mac='', domain='',
           identity_file=os.environ.get('TOLLGATE_SSH_KEY') or None)
result = firstboot_reset(r, expected_mac='${TOLLGATE_EXPECTED_MAC:-}')
print(f'result={result[\"success\"]}')
sys.exit(0 if result['success'] else 1)
"
fi

# ── Deploy branch ───────────────────────────────────────────────────────
echo "==> Deploying branch $TOLLGATE_BRANCH..."
python3 -c "
import os, sys, logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')
from lib.router import Router
from lib.deploy import deploy_branch
r = Router(host='${ROUTER_IP}', phone_ip='', phone_mac='', domain='',
           identity_file=os.environ.get('TOLLGATE_SSH_KEY') or None)
result = deploy_branch(r, '${TOLLGATE_BRANCH}', arch='${TOLLGATE_ROUTER_ARCH:-aarch64_cortex-a53}',
                       run_id=None, force=True, reboot=False,
                       repo='${ARTIFACT_REPO:-}')
print(f'version={result[\"installed_version\"]} health={result[\"health_code\"]} success={result[\"success\"]}')
sys.exit(0 if result['success'] else 1)
"

# ── Run API tests ───────────────────────────────────────────────────────
echo ""
echo "==> Running $TEST_TYPE tests..."

cd "$REPO_DIR/tests"

if [[ "$TEST_TYPE" == "api" ]]; then
  pytest_output=$(python3 -m pytest api/ -v --tb=short --junitxml="$RUN_DIR/raw/junit.xml" 2>&1)
  exit_code=$?
  echo "$pytest_output" | tee "$RUN_DIR/raw/output.log"
else
  pytest_output=$(python3 -m pytest -v --tb=short --junitxml="$RUN_DIR/raw/junit.xml" 2>&1)
  exit_code=$?
  echo "$pytest_output" | tee "$RUN_DIR/raw/output.log"
fi

# ── Parse JUnit XML for test counts ─────────────────────────────────────
PASSED=0
FAILED=0
SKIPPED=0
DURATION_MS=0

if [[ -f "$RUN_DIR/raw/junit.xml" ]]; then
  PASSED=$(python3 - "$RUN_DIR/raw/junit.xml" <<'PYEOF'
import sys, xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
count = 0
for testsuite in root.findall('.//testsuite'):
  tests = int(testsuite.get('tests', 0))
  failures = int(testsuite.get('failures', 0))
  errors = int(testsuite.get('errors', 0))
  skipped = int(testsuite.get('skipped', 0))
  time = float(testsuite.get('time', 0))
  count += tests
  PASSED += tests - failures - errors - skipped
  FAILED += failures + errors
  SKIPPED += skipped
  DURATION_MS += time * 1000
print(PASSED, FAILED, SKIPPED, DURATION_MS)
PYEOF
  )
  IFS=' ' read -r PASSED FAILED SKIPPED DURATION_MS <<< "$PASSED"
  echo "==> Results: $PASSED passed, $FAILED failed, $SKIPPED skipped (${DURATION_MS}ms)"
else
  echo "WARNING: junit.xml not found — counts will be zero" >&2
fi

# ── Generate HTML report ─────────────────────────────────────────────────
echo "==> Generating HTML report..."
python3 - "$RUN_DIR/raw/output.log" "$RUN_DIR/report/index.html" "$PASSED" "$FAILED" "$SKIPPED" "$DURATION_MS" "$TOLLGATE_COMMIT" "$TOLLGATE_BRANCH" "$TOLLGATE_PR" <<'PYEOF'
import sys
output_log, report_file = sys.argv[1], sys.argv[2]
passed, failed, skipped = sys.argv[3], sys.argv[4], sys.argv[5]
duration_ms = sys.argv[6]
commit, branch, pr = sys.argv[7], sys.argv[8], sys.argv[9]

with open(report_file, 'w') as f:
    f.write('<!DOCTYPE html>\n<html lang="en">\n<head>\n')
    f.write('  <meta charset="utf-8">\n')
    f.write('  <meta name="viewport" content="width=device-width, initial-scale=1">\n')
    f.write(f'  <title>PR Test Report - {commit[:12]}</title>\n')
    f.write('  <style>\n')
    f.write('    body { font-family: monospace; line-height: 1.4; padding: 20px; background: #f5f5f5; }\n')
    f.write('    .pass { color: #137333; font-weight: bold; }\n')
    f.write('    .fail { color: #d93025; font-weight: bold; }\n')
    f.write('    .skip { color: #80868b; font-style: italic; }\n')
    f.write('    .summary { background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 20px; }\n')
    f.write('    .summary h1 { margin: 0 0 10px 0; }\n')
    f.write('    .summary .stats { display: grid; grid-template-columns: auto auto; gap: 8px 16px; }\n')
    f.write('    .summary .stats .label { color: #666; }\n')
    f.write('    .output { background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); white-space: pre-wrap; overflow-x: auto; }\n')
    f.write('  </style>\n')
    f.write('</head>\n<body>\n')
    f.write('  <div class="summary">\n')
    pr_label = f'PR #{pr}' if pr and pr != 'None' else branch
    f.write(f'    <h1>Test Report: {pr_label} ({commit[:12]})</h1>\n')
    f.write('    <div class="stats">\n')
    f.write(f'      <span class="label">Passed:</span><span class="value pass">{passed}</span>\n')
    f.write(f'      <span class="label">Failed:</span><span class="value fail">{failed}</span>\n')
    f.write(f'      <span class="label">Skipped:</span><span class="value">{skipped}</span>\n')
    f.write(f'      <span class="label">Duration:</span><span class="value">{duration_ms}ms</span>\n')
    f.write('    </div>\n')
    f.write('  </div>\n')
    f.write('  <div class="output">\n')
    try:
        f.write(open(output_log).read())
    except Exception:
        f.write('(output log not available)')
    f.write('  </div>\n')
    f.write('</body>\n</html>\n')
PYEOF

# ── Write run.json ─────────────────────────────────────────────────────
TEST_SUITE_COMMIT=$(git -C "$REPO_DIR" rev-parse HEAD)
ISO_TIMESTAMP=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

python3 - "$TOLLGATE_COMMIT" "${TOLLGATE_BRANCH:-}" "${TOLLGATE_PR:-}" \
  "$TEST_SUITE_COMMIT" "$ROUTER_ID" "$ROUTER_IP" "${TOLLGATE_VIEWPORT:-desktop}" \
  "$ISO_TIMESTAMP" "$PASSED" "$FAILED" "$SKIPPED" "0" "$DURATION_MS" \
  "$RUN_DIR/run.json" <<'PYEOF'
import json, sys
a = sys.argv[1:]
run = {
    'tollgate_commit': a[0],
    'tollgate_branch': a[1] if a[1] else None,
    'tollgate_pr': int(a[2]) if a[2] else None,
    'test_suite_commit': a[3],
    'test_type': 'api',
    'router_id': a[4],
    'router_ip': a[5],
    'viewport': a[6],
    'timestamp': a[7],
    'passed': int(a[8]),
    'failed': int(a[9]),
    'skipped': int(a[10]),
    'flaky': 0,
    'duration_ms': float(a[11]) if '.' in a[11] else int(a[11]),
}
with open(a[12], 'w') as f:
    json.dump(run, f, indent=2)
    f.write('\n')
PYEOF

echo "==> Wrote $RUN_DIR/run.json"

# ── Publish report (if requested) ─────────────────────────────────────────
if [[ "$PUBLISH" == "true" ]]; then
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
echo "  Branch:     ${TOLLGATE_BRANCH:-N/A}"
echo "  Router:     ${ROUTER_ID:-$ROUTER_IP} ($ROUTER_IP)"
echo "  Passed:     $PASSED"
echo "  Failed:     $FAILED"
echo "  Skipped:    $SKIPPED"
DURATION_SEC=$(python3 -c "print(int(float('$DURATION_MS') / 1000))")
echo "  Duration:   ${DURATION_SEC}s"
echo "  Run dir:    $RUN_DIR"
echo "====================================================================="

exit $exit_code
