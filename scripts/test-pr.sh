#!/usr/bin/env bash
set -euo pipefail

# ── Unified PR testing workflow ────────────────────────────────────────────
# Deploy a PR branch to router, run API tests, generate reports.
#
# Usage:
#   ./scripts/test-pr.sh --pr <N> [--reset] [--test api|all] [--publish] [--router ID] [--backend go|rust]
#   ./scripts/test-pr.sh --branch <NAME> [--reset] [--test api|all] [--publish] [--router ID] [--backend go|rust]
#
# Required environment variables:
#   TOLLGATE_LUCI_PASSWORD — Router SSH and LuCI password
#
# Optional environment variables:
#   ROUTER_IP, TOLLGATE_SSH_HOST — Router IP for SSH
#   TOLLGATE_SSH_KEY — SSH key path (default: ~/.ssh/id_ed25519)
#   TOLLGATE_ROUTER_ID — Router ID (default: from .env)
#   TOLLGATE_BACKEND — Backend type: go (default) or rust
# ───────────────────────────────────────────────────────────────────────────

# ── Parse CLI args ───────────────────────────────────────────────────────

PR_NUM=""
BRANCH=""
RESET=false
TEST_TYPE="api"
PUBLISH=false
ROUTER_ID=""
ARTIFACT_REPO=""
BACKEND="${TOLLGATE_BACKEND:-go}"

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
    --backend)
      BACKEND="$2"
      if [[ "$BACKEND" != "go" && "$BACKEND" != "rust" ]]; then
        echo "ERROR: --backend must be 'go' or 'rust'" >&2
        exit 1
      fi
      shift 2
      ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      echo "Usage: $0 --pr <N> | --branch <NAME> [--reset] [--test api|all] [--publish] [--backend go|rust] [--router ID]" >&2
      exit 1
      ;;
  esac
done

export TOLLGATE_BACKEND="$BACKEND"

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

# ── Resolve backend config ──────────────────────────────────────────────
RUST_REPO="Amperstrand/tollgate-rs-ai-research-and-experiments"
GO_REPO="OpenTollGate/tollgate-module-basic-go"
if [[ "$BACKEND" == "rust" ]]; then
  DEFAULT_REPO="$RUST_REPO"
else
  DEFAULT_REPO="$GO_REPO"
fi

# ── Resolve PR to branch + SHA ───────────────────────────────────────────

if [[ -n "$PR_NUM" ]]; then
  LOOKUP_REPO="${ARTIFACT_REPO:-$DEFAULT_REPO}"
  echo "==> Resolving PR $PR_NUM..."
  BRANCH=$(gh pr view "$PR_NUM" --repo "$LOOKUP_REPO" --json headRefName --jq '.headRefName')
  COMMIT_SHA=$(gh pr view "$PR_NUM" --repo "$LOOKUP_REPO" --json headRefOid --jq '.headRefOid')
  FORK_REPO=$(gh pr view "$PR_NUM" --repo "$LOOKUP_REPO" --json headRepository --jq '.headRepository.owner.login + "/" + .headRepository.name' 2>/dev/null || echo "")
  TOLLGATE_COMMIT="${COMMIT_SHA:0:12}"
  TOLLGATE_PR="$PR_NUM"
  TOLLGATE_BRANCH="$BRANCH"
  if [[ -n "$FORK_REPO" && -z "$ARTIFACT_REPO" ]]; then
    ARTIFACT_REPO="$FORK_REPO"
  fi
else
  TOLLGATE_BRANCH="$BRANCH"
  LOOKUP_REPO="${ARTIFACT_REPO:-$DEFAULT_REPO}"
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
_ssh_cmd() {
  if [[ -n "$SSH_CHECK_KEY" ]]; then
    ssh -i "$SSH_CHECK_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=5 -o LogLevel=ERROR "root@${ROUTER_IP}" "$1"
  else
    export SSHPASS="${TOLLGATE_SSH_PASSWORD:-$TOLLGATE_LUCI_PASSWORD}"
    sshpass -e ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=5 -o LogLevel=ERROR "root@${ROUTER_IP}" "$1"
  fi
}
_check_ssh() { _ssh_cmd 'echo ok' &>/dev/null; }
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

# ── Auto-detect architecture from router ─────────────────────────────────
if [[ -z "${TOLLGATE_ROUTER_ARCH:-}" ]]; then
  DETECTED_ARCH=$(_ssh_cmd "opkg print-architecture 2>/dev/null | grep -v 'all\|noarch' | tail -1 | awk '{print \$2}'" 2>/dev/null || true)
  if [[ -n "$DETECTED_ARCH" ]]; then
    TOLLGATE_ROUTER_ARCH="$DETECTED_ARCH"
    echo "==> Detected router arch: $TOLLGATE_ROUTER_ARCH"
  else
    TOLLGATE_ROUTER_ARCH="aarch64_cortex-a53"
    echo "==> WARNING: Could not detect arch, defaulting to $TOLLGATE_ROUTER_ARCH" >&2
  fi
fi

# ── Run directory (canonical layout) ────────────────────────────────────
TIMESTAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
GIT_SHA=$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")
RUN_ID="${TIMESTAMP}-${GIT_SHA}"
RESULTS_DIR="$REPO_DIR/results/$RUN_ID"
mkdir -p "$RESULTS_DIR/raw/api" "$RESULTS_DIR/report" "$RESULTS_DIR/artifacts"
STARTED_AT=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

echo "==> Run directory: $RESULTS_DIR"
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
from lib.backend import BackendConfig
backend = BackendConfig('${BACKEND}')
r = Router(host='${ROUTER_IP}', phone_ip='', phone_mac='', domain='',
           identity_file=os.environ.get('TOLLGATE_SSH_KEY') or None,
           backend=backend)
result = deploy_branch(r, '${TOLLGATE_BRANCH}', arch='${TOLLGATE_ROUTER_ARCH:-}',
                       run_id=None, force=True, reboot=False,
                       repo='${ARTIFACT_REPO:-}', backend=backend)
print(f'version={result[\"installed_version\"]} health={result[\"health_code\"]} success={result[\"success\"]}')
sys.exit(0 if result['success'] else 1)
"

# ── Run tests ────────────────────────────────────────────────────────────
echo ""
echo "==> Running $TEST_TYPE tests..."

cd "$REPO_DIR/tests"

exit_code=0
if [[ "$TEST_TYPE" == "api" ]]; then
  python3 -m pytest api/ -v --tb=short --backend="$BACKEND" \
    --junitxml="$RESULTS_DIR/raw/api/junit.xml" \
    --html="$RESULTS_DIR/raw/api/report.html" \
    --self-contained-html \
    2>&1 | tee "$RESULTS_DIR/raw/api/output.log" || exit_code=$?
else
  python3 -m pytest -v --tb=short --backend="$BACKEND" \
    --junitxml="$RESULTS_DIR/raw/api/junit.xml" \
    --html="$RESULTS_DIR/raw/api/report.html" \
    --self-contained-html \
    2>&1 | tee "$RESULTS_DIR/raw/api/output.log" || exit_code=$?
fi

# ── Collect results ──────────────────────────────────────────────────────
FINISHED_AT=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

echo "==> Collecting results..."
python3 "$SCRIPT_DIR/collect-results.py" \
  --run-dir "$RESULTS_DIR" \
  --pytest "api=raw/api/junit.xml" \
  --run-id "$RUN_ID" \
  --sut-repo "${ARTIFACT_REPO:-$DEFAULT_REPO}" \
  --sut-commit "$COMMIT_SHA" \
  --sut-branch "$TOLLGATE_BRANCH" \
  --sut-pr "${TOLLGATE_PR:-}" \
  --sut-backend "$BACKEND" \
  --suite-commit "$(git -C "$REPO_DIR" rev-parse HEAD)" \
  --router-id "$ROUTER_ID" \
  --router-model "${TOLLGATE_ROUTER_MODEL:-}" \
  --router-arch "${TOLLGATE_ROUTER_ARCH:-}" \
  --client-type "${TOLLGATE_CLIENT_TYPE:-}" \
  --viewport "${TOLLGATE_VIEWPORT:-desktop}" \
  --test-plan "pr-${TEST_TYPE}" \
  --started-at "$STARTED_AT" \
  --finished-at "$FINISHED_AT" \
  --allow-failures || true

# ── Render HTML report ───────────────────────────────────────────────────
echo "==> Rendering HTML report..."
python3 "$SCRIPT_DIR/render-report.py" --run-dir "$RESULTS_DIR" || true

# ── Publish report (if requested) ────────────────────────────────────────
if [[ "$PUBLISH" == "true" ]]; then
  echo ""
  echo "==> Publishing report..."
  "$SCRIPT_DIR/publish-report.sh" "$RESULTS_DIR" || true
fi

# ── Summary ──────────────────────────────────────────────────────────────
echo ""
echo "====================================================================="
echo "  TEST SUMMARY"
echo "====================================================================="

# Read counts from the generated run.json
if [[ -f "$RESULTS_DIR/run.json" ]]; then
  SUMMARY_COUNTS=$(python3 -c "
import json, sys
run = json.load(open('$RESULTS_DIR/run.json'))
c = run.get('counts', {})
d = run.get('duration_ms', 0)
print(f\"{c.get('passed', 0)} {c.get('failed', 0)} {c.get('skipped', 0)} {d}\")
" 2>/dev/null || echo "0 0 0 0")
  IFS=' ' read -r PASSED FAILED SKIPPED DURATION_MS <<< "$SUMMARY_COUNTS"
  DURATION_SEC=$(python3 -c "print(int(${DURATION_MS:-0}) // 1000)" 2>/dev/null || echo "?")
else
  PASSED="?"
  FAILED="?"
  SKIPPED="?"
  DURATION_SEC="?"
fi

echo "  Tollgate:   ${TOLLGATE_COMMIT:0:12}"
echo "  Backend:    $BACKEND"
echo "  Branch:     ${TOLLGATE_BRANCH:-N/A}"
if [[ -n "${TOLLGATE_PR:-}" ]]; then
  echo "  PR:         #$TOLLGATE_PR"
fi
echo "  Router:     ${ROUTER_ID:-$ROUTER_IP} ($ROUTER_IP)"
echo "  Passed:     $PASSED"
echo "  Failed:     $FAILED"
echo "  Skipped:    $SKIPPED"
echo "  Duration:   ${DURATION_SEC}s"
echo "  Run dir:    $RESULTS_DIR"
echo "  Report:     $RESULTS_DIR/report/index.html"
echo "====================================================================="

exit $exit_code
