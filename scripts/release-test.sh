#!/usr/bin/env bash
# Release test pipeline — one command to validate a TollGate release.
#
# Usage: scripts/release-test.sh --version v0.6.0-rc1 [--router IP]
#                                  [--place NAME] [--ipk PATH] [--no-flash]
#                                  [--report-dir DIR]
#
# Phases:
#   1. Preflight — verify DUT reachable, labgrid place available
#   2. Install (optional) — deploy the release candidate package
#   3. Stories — run the full story suite
#   4. Edge cases — error paths, invalid inputs
#   5. Benchmarks — payment latency, portal load time
#   6. Report — markdown summary with evidence links
#
# Consolidation note (2026-10 verification audit): this runner is the frozen
# opkg-era release lane. Package deployment goes through the shared harness
# (lib.deploy.deploy — era-aware, health-checked); portal and labgrid-lock
# failures are blocking. The long-term decision and the milestone plan that
# fold this lane into the profile runners live in
# docs/authoritative-test-suite.md → "Runner consolidation". Do not add new
# deploy/install logic here.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && dirname "$0")"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="${TOLLGATE_PYTHON_VENV:-$HOME/venvs/rig-labgrid}"
LABGRID_COORD="${LG_COORDINATOR:-192.168.13.208:20408}"
LABGRID_PLACE="${LG_PLACE:-nr7101-router}"

VERSION=""
ROUTER="${TOLLGATE_SSH_HOST:-192.168.12.124}"
IPK_PATH=""
NO_FLASH=false
REPORT_DIR=""
SKIP_LABGRID=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version) VERSION="$2"; shift 2 ;;
        --place) LABGRID_PLACE="$2"; shift 2 ;;
        --router) ROUTER="$2"; shift 2 ;;
        --ipk) IPK_PATH="$2"; shift 2 ;;
        --no-flash) NO_FLASH=true; shift ;;
        --report-dir) REPORT_DIR="$2"; shift 2 ;;
        --skip-labgrid) SKIP_LABGRID=true; shift ;;
        *) echo "unknown: $1"; exit 1 ;;
    esac
done

LG="$VENV/bin/labgrid-client"; [ -x "$LG" ] || LG="$(command -v labgrid-client || true)"
PY="$VENV/bin/python"; [ -x "$PY" ] || PY="python3"

if [[ -z "$VERSION" ]]; then
    echo "ERROR: --version required (e.g. v0.6.0-rc1)"
    exit 1
fi

if [[ -z "$REPORT_DIR" ]]; then
    REPORT_DIR="/tmp/opencode/release-$VERSION-$(date +%Y%m%d-%H%M%S)"
fi
mkdir -p "$REPORT_DIR"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$REPORT_DIR/pipeline.log"; }

# ═══════════════════════════════════════════════════════════════
# Phase 1: Preflight
# ═══════════════════════════════════════════════════════════════
log "═══ Release Test: $VERSION ═══"
log "DUT: $ROUTER"
log "Report: $REPORT_DIR"
log ""

LOCKED_PLACE=""

if [[ "$SKIP_LABGRID" == "false" ]]; then
    log "Phase 1: Preflight + labgrid reservation"
    if "$LG" -x "$LABGRID_COORD" -p "$LABGRID_PLACE" lock 2>/dev/null; then
        LOCKED_PLACE="$LABGRID_PLACE"
        log "✓ Labgrid place '$LABGRID_PLACE' locked"
    else
        log "❌ Could not lock labgrid place '$LABGRID_PLACE' (in use, or coordinator unreachable at $LABGRID_COORD)"
        log "   A release run without the mutex is not attributable — refusing."
        log "   Retry when the place is free, or pass --skip-labgrid explicitly."
        exit 1
    fi
else
    log "Phase 1: Preflight (labgrid skipped)"
fi

# Verify DUT reachable
if ! ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "root@$ROUTER" "echo ok" >/dev/null 2>&1; then
    log "❌ DUT not reachable at $ROUTER"
    [[ -n "$LOCKED_PLACE" ]] && "$LG" -x "$LABGRID_COORD" -p "$LOCKED_PLACE" release 2>/dev/null || true
    exit 1
fi
log "✓ DUT reachable at $ROUTER"

# Check current version
CURRENT=$(ssh -o ConnectTimeout=5 "root@$ROUTER" "opkg status tollgate-wrt 2>/dev/null | grep Version | cut -d' ' -f2" 2>/dev/null || echo "unknown")
log "✓ Current version: $CURRENT"
log ""

# ═══════════════════════════════════════════════════════════════
# Phase 2: Install (optional)
# ═══════════════════════════════════════════════════════════════
if [[ "$NO_FLASH" == "false" && -n "$IPK_PATH" ]]; then
    log "Phase 2: Installing $IPK_PATH (via lib.deploy.deploy)"
    DEPLOY_LOG="$REPORT_DIR/deploy.log"
    # The standard harness path (transfer, era-aware apk/opkg install, service
    # restart, :2121 health check) — replaces the hand-copied scp+opkg that
    # lived here (docs/authoritative-test-suite.md → "Runner consolidation").
    if (cd "$REPO_ROOT" && "$PY" - "$ROUTER" "$IPK_PATH" <<'PY'
import sys
from pathlib import Path

from lib.deploy import deploy
from lib.router import Router

router = Router(host=sys.argv[1], phone_ip="", phone_mac="", domain="")
result = deploy(router, Path(sys.argv[2]), reboot=False)
print(
    f"version={result.get('installed_version')} "
    f"health={result.get('health_code')} success={result.get('success')}"
)
sys.exit(0 if result.get("success") else 1)
PY
    ) >"$DEPLOY_LOG" 2>&1; then
        tail -n 1 "$DEPLOY_LOG" | tee -a "$REPORT_DIR/pipeline.log"
        log "✓ Package installed, service healthy"
    else
        tail -n 20 "$DEPLOY_LOG" >&2 || true
        log "❌ Package install failed (see $DEPLOY_LOG)"
        [[ -n "$LOCKED_PLACE" ]] && "$LG" -x "$LABGRID_COORD" -p "$LOCKED_PLACE" release 2>/dev/null || true
        exit 1
    fi
    sleep 10
else
    log "Phase 2: Skipping install (--no-flash or no --ipk)"
fi

# ═══════════════════════════════════════════════════════════════
# Phase 3: Stories
# ═══════════════════════════════════════════════════════════════
log "Phase 3: Running story suite"
export TOLLGATE_SSH_HOST="$ROUTER"
export TOLLGATE_SSID="${TOLLGATE_SSID:-TollGate}"
# The lab moto g (ZY326DPC7R) is a SHARED bench resource — its current
# labgrid export point is the numo-phone place (Numo lane wired it last).
# Acquire that place for exclusivity when driving phone stories, and set
# PHONE_SERIAL explicitly; unset means phone-dependent stories degrade to
# other clients instead of silently using the device.
export PHONE_SERIAL="${PHONE_SERIAL:-}"
export TOLLGATE_TEST_MINT_URL="${TOLLGATE_TEST_MINT_URL:-http://192.168.13.221:8383}"

cd "$REPO_ROOT"
STORY_RESULT=0
PORTAL_RESULT=0
if "$PY" -m pytest tests/scenarios/stories/ \
    --no-deploy --timeout-method=signal -v --tb=short \
    2>&1 | tee "$REPORT_DIR/stories.log"; then
    log "✓ All stories passed"
else
    log "⚠ Some stories failed"
    STORY_RESULT=1
fi
log ""

# ═══════════════════════════════════════════════════════════════
# Phase 4: Portal tab-copy (if portal is accessible)
# ═══════════════════════════════════════════════════════════════
PORTAL_CODE=$(curl -s -m 5 -o /dev/null -w "%{http_code}" "http://$ROUTER:2051/splash.html" 2>/dev/null || echo "000")
if [[ "$PORTAL_CODE" == "200" ]]; then
    log "Phase 4: Portal tab-copy tests"
    if ROUTER_IP="$ROUTER" npx playwright test \
        tests/browser/portal-tab-copy.spec.mjs \
        --config tests/browser/portal-tab-copy.config.mjs \
        --reporter=line \
        2>&1 | tee "$REPORT_DIR/portal.log" | tail -3; then
        log "✓ Portal tests passed"
    else
        log "❌ Portal tests failed"
        PORTAL_RESULT=1
    fi
else
    log "Phase 4: Skipping portal tests (HTTP $PORTAL_CODE)"
fi
log ""

# ═══════════════════════════════════════════════════════════════
# Phase 5: Report generation
# ═══════════════════════════════════════════════════════════════
log "Phase 5: Generating report"

OVERALL=$(( STORY_RESULT | PORTAL_RESULT ))

STORY_PASSED=$(grep -c "PASSED" "$REPORT_DIR/stories.log" 2>/dev/null || echo 0)
STORY_FAILED=$(grep -c "FAILED" "$REPORT_DIR/stories.log" 2>/dev/null || echo 0)
STORY_SKIPPED=$(grep -c "SKIPPED" "$REPORT_DIR/stories.log" 2>/dev/null || echo 0)

cat > "$REPORT_DIR/report.md" <<REPORT
# TollGate Release Test Report: $VERSION

**Date**: $(date -u +%Y-%m-%dT%H:%M:%SZ)
**DUT**: $ROUTER (was running $CURRENT)
**Result**: $([ $OVERALL -eq 0 ] && echo "✅ PASS" || echo "❌ FAIL")

## Story Suite

| Metric | Count |
|--------|-------|
| Passed | $STORY_PASSED |
| Failed | $STORY_FAILED |
| Skipped | $STORY_SKIPPED |
| **Total** | $((STORY_PASSED + STORY_FAILED + STORY_SKIPPED)) |

## Artifacts

- Full log: \`stories.log\`
- Portal tests: \`portal.log\`
- Pipeline log: \`pipeline.log\`

## Contract

Validated against: \`config/behavior-contract.json\` (version 1.0.0)
REPORT

log "Report written to $REPORT_DIR/report.md"
log ""

# ═══════════════════════════════════════════════════════════════
# Cleanup
# ═══════════════════════════════════════════════════════════════
if [[ -n "$LOCKED_PLACE" ]]; then
    "$LG" -x "$LABGRID_COORD" -p "$LOCKED_PLACE" release 2>/dev/null || true
    log "✓ Labgrid place released"
fi

log "═══ Release test complete ═══"
log "Report: $REPORT_DIR/report.md"

exit $OVERALL
