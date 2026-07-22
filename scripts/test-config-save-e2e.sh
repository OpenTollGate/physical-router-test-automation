#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# E2E test for tollgate config save round-trip
#
# Usage:  ./scripts/test-config-save-e2e.sh <router_host>
#
# Tests:
#   1. Save current config → success response
#   2. Disk file matches saved values
#   3. Modify + save → new values persisted
#   4. Save minimal valid config → success
#   5. Save invalid JSON → error
#   6. Full round-trip: get → extract → modify → save → get → verify
#   7. Restart persistence after save
# ---------------------------------------------------------------------------

ROUTER="${1:?Usage: $0 <router_host>}"
SSH="ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new root@$ROUTER"

BOLD='\033[1m'
GREEN='\033[32m'
RED='\033[31m'
CYAN='\033[36m'
YELLOW='\033[33m'
RESET='\033[0m'

PASS=0
FAIL=0
SKIP=0

pass() { echo "  ${GREEN}PASS${RESET}: $1"; PASS=$((PASS+1)); }
fail() { echo "  ${RED}FAIL${RESET}: $1"; FAIL=$((FAIL+1)); }
skip() { echo "  ${YELLOW}SKIP${RESET}: $1"; SKIP=$((SKIP+1)); }
section() { echo ""; echo "${CYAN}--- $1 ---${RESET}"; }

disk_field() {
    $SSH "jsonfilter -e '@.$1' < /etc/tollgate/config.json" 2>&1 || true
}

ORIG_LOGLEVEL=$($SSH "tollgate --json config get" 2>&1 | grep -oP '"log_level"\s*:\s*"\K[^"]*' | head -1) || ORIG_LOGLEVEL="info"

cleanup() {
    echo ""
    echo "${CYAN}Restoring original config...${RESET}"
    $SSH "tollgate --json config set log_level $ORIG_LOGLEVEL" > /dev/null 2>&1 || true
    echo "${GREEN}Done.${RESET}"
}
trap cleanup EXIT

# =========================================================================
#  Test 1: Save current config — success response
# =========================================================================
section "Test 1: Save current config (no-op round-trip)"

OUT=$($SSH 'tollgate --json config save "$(tollgate --json config get | jsonfilter -e '"'"'@.data.config'"'"')"' 2>&1) || true
if echo "$OUT" | grep -qP '"success"\s*:\s*true'; then
    pass "config save returns success"
else
    fail "config save did not return success (got: $(echo "$OUT" | head -c 200))"
fi

# =========================================================================
#  Test 2: Disk file matches saved values
# =========================================================================
section "Test 2: Disk file matches after save"

OUT=$($SSH 'tollgate --json config save "$(tollgate --json config get | jsonfilter -e '"'"'@.data.config'"'"')"' 2>&1) || true
if echo "$OUT" | grep -qP '"success"\s*:\s*true'; then
    DISK_METRIC=$(disk_field "metric") || true
    CLI_METRIC=$($SSH "tollgate --json config get" 2>&1 | grep -oP '"metric"\s*:\s*"\K[^"]+' | head -1) || true
    if [ "$DISK_METRIC" = "$CLI_METRIC" ]; then
        pass "disk metric ($DISK_METRIC) matches CLI ($CLI_METRIC)"
    else
        fail "disk metric ($DISK_METRIC) != CLI ($CLI_METRIC)"
    fi

    DISK_VERSION=$(disk_field "config_version") || true
    if [ -n "$DISK_VERSION" ]; then
        pass "disk has config_version: $DISK_VERSION"
    else
        fail "disk missing config_version"
    fi
else
    skip "disk verification (save failed)"
fi

# =========================================================================
#  Test 3: Modify field + save → verify new value on disk
# =========================================================================
section "Test 3: Modify + save → new value persisted"

$SSH "tollgate --json config set log_level error" > /dev/null 2>&1 || true
OUT=$($SSH 'tollgate --json config save "$(tollgate --json config get | jsonfilter -e '"'"'@.data.config'"'"')"' 2>&1) || true
if echo "$OUT" | grep -qP '"success"\s*:\s*true'; then
    DISK_LEVEL=$(disk_field "log_level") || true
    if [ "$DISK_LEVEL" = "error" ]; then
        pass "modified log_level=error persisted to disk"
    else
        fail "disk log_level=$DISK_LEVEL (expected error)"
    fi
else
    fail "save after modify failed (got: $(echo "$OUT" | head -c 200))"
fi

# =========================================================================
#  Test 4: Save minimal valid config
# =========================================================================
section "Test 4: Save minimal valid config"

MINIMAL='{"config_version":"v0.0.7","metric":"bytes","step_size":22020096,"accepted_mints":[{"url":"https://testnut-compat.mints.orangesync.tech","min_balance":64,"balance_tolerance_percent":10,"payout_interval_seconds":60,"min_payout_amount":128,"price_per_step":1,"price_unit":"sat","purchase_min_steps":0}],"profit_share":[{"factor":0.8,"identity":"operator"},{"factor":0.2,"identity":"treasury"}]}'
OUT=$($SSH "tollgate --json config save '$MINIMAL'" 2>&1) || true
if echo "$OUT" | grep -qP '"success"\s*:\s*true'; then
    pass "minimal config save succeeds"
    DISK_METRIC=$(disk_field "metric") || true
    if [ "$DISK_METRIC" = "bytes" ]; then
        pass "minimal config metric=bytes on disk"
    else
        fail "minimal save: disk metric=$DISK_METRIC (expected bytes)"
    fi
else
    fail "minimal config save failed (got: $(echo "$OUT" | head -c 200))"
fi

# =========================================================================
#  Test 5: Save invalid JSON → error
# =========================================================================
section "Test 5: Save invalid JSON"

OUT=$($SSH "tollgate --json config save 'not-json-at-all'" 2>&1) || true
if echo "$OUT" | grep -qi "error\|invalid\|failed\|parse"; then
    pass "rejects invalid JSON"
else
    if echo "$OUT" | grep -qP '"success"\s*:\s*true'; then
        fail "accepted invalid JSON as valid"
    else
        pass "rejects invalid JSON (non-success response)"
    fi
fi

OUT=$($SSH "tollgate --json config save '{invalid json}'" 2>&1) || true
if echo "$OUT" | grep -qi "error\|invalid\|failed\|parse"; then
    pass "rejects malformed JSON"
else
    if echo "$OUT" | grep -qP '"success"\s*:\s*true'; then
        fail "accepted malformed JSON as valid"
    else
        pass "rejects malformed JSON (non-success response)"
    fi
fi

# =========================================================================
#  Test 6: Full round-trip — get → modify → save → get → verify
# =========================================================================
section "Test 6: Full round-trip: get → modify → save → get → verify"

CURRENT=$($SSH 'tollgate --json config get | jsonfilter -e '"'"'@.data.config'"'"'' 2>&1) || true
if [ -z "$CURRENT" ]; then
    fail "could not extract current config for round-trip"
else
    pass "extracted current config for round-trip"
fi

$SSH "tollgate --json config set log_level debug" > /dev/null 2>&1 || true

OUT=$($SSH 'tollgate --json config save "$(tollgate --json config get | jsonfilter -e '"'"'@.data.config'"'"')"' 2>&1) || true
if echo "$OUT" | grep -qP '"success"\s*:\s*true'; then
    pass "round-trip save succeeded"
else
    fail "round-trip save failed (got: $(echo "$OUT" | head -c 200))"
fi

GET_LEVEL=$($SSH "tollgate --json config get" 2>&1 | grep -oP '"log_level"\s*:\s*"\K[^"]*' | head -1) || true
if [ "$GET_LEVEL" = "debug" ]; then
    pass "round-trip: config get shows log_level=debug"
else
    fail "round-trip: config get shows log_level=$GET_LEVEL (expected debug)"
fi

DISK_LEVEL=$(disk_field "log_level") || true
if [ "$DISK_LEVEL" = "debug" ]; then
    pass "round-trip: disk shows log_level=debug"
else
    fail "round-trip: disk shows log_level=$DISK_LEVEL (expected debug)"
fi

# =========================================================================
#  Test 7: Restart persistence — save → restart → verify
# =========================================================================
section "Test 7: Restart persistence after save"

$SSH "tollgate --json config set log_level warn" > /dev/null 2>&1 || true
$SSH 'tollgate --json config save "$(tollgate --json config get | jsonfilter -e '"'"'@.data.config'"'"')"' > /dev/null 2>&1 || true

echo "  Restarting tollgate-wrt service..."
$SSH "/etc/init.d/tollgate-wrt restart" 2>&1 || true
echo "  Waiting for service startup (up to 60s)..."
for i in $(seq 1 30); do
    HEALTH_CHECK=$($SSH "tollgate --json health 2>&1" 2>&1) || true
    if echo "$HEALTH_CHECK" | grep -q '"ok"'; then
        echo "  Service healthy after $((i * 2))s"
        break
    fi
    sleep 2
done

GET_LEVEL=$($SSH "tollgate --json config get" 2>&1 | grep -oP '"log_level"\s*:\s*"\K[^"]*' | head -1) || true
if [ "$GET_LEVEL" = "warn" ]; then
    pass "log_level=warn persisted after service restart"
else
    fail "log_level=$GET_LEVEL after restart (expected warn)"
fi

HEALTH=$($SSH "tollgate --json health" 2>&1) || true
if echo "$HEALTH" | grep -q '"ok"'; then
    pass "daemon healthy after restart"
else
    fail "daemon not healthy after restart (got: $(echo "$HEALTH" | head -c 200))"
fi

# =========================================================================
#  Summary
# =========================================================================
echo ""
echo "${BOLD}=========================================${RESET}"
echo "${BOLD}  Config Save E2E: ${GREEN}$PASS passed${RESET}, ${RED}$FAIL failed${RESET}, ${YELLOW}$SKIP skipped${RESET}"
echo "${BOLD}=========================================${RESET}"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
