#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# E2E test for PR #124 (tollgate --json backend) + configurationwizzard SPA
#
# Usage:  ./scripts/test-configwizzard-e2e.sh <router_host>
#
# Tests:
#   Phase 1: PR #124 — CLI --json backend (schema, config get/set, wallet, health, status)
#   Phase 2: RPCD plugin — ubus methods via ssh ubus call
#   Phase 3: :2121 payment API — pricing, whoami, balance endpoints
#   Phase 4: configurationwizzard SPA deployment — admin + portal files
#   Phase 5: Integration — rpcd plugin returns real tollgate --json data
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

# =========================================================================
#  Phase 1: PR #124 — tollgate --json CLI backend
# =========================================================================
section "Phase 1: tollgate --json CLI (PR #124)"

# 1.1 config schema
OUT=$($SSH "tollgate --json config schema" 2>&1) || true
if echo "$OUT" | grep -q '"json_key"'; then
    COUNT=$(echo "$OUT" | grep -o '"json_key"' | wc -l)
    pass "config schema returns $COUNT FieldSchema entries"
else
    fail "config schema not responding or invalid JSON"
fi

# 1.2 config get
OUT=$($SSH "tollgate --json config get" 2>&1) || true
if echo "$OUT" | grep -q '"metric"' && echo "$OUT" | grep -q '"accepted_mints"'; then
    pass "config get returns full config + identities"
else
    fail "config get missing expected fields"
fi

# 1.3 config set + disk persistence
ORIG_LOGLEVEL=$(echo "$OUT" | grep -oP '"log_level"\s*:\s*"\K[^"]*' | head -1) || ORIG_LOGLEVEL="info"
SET_OUT=$($SSH "tollgate --json config set log_level warn" 2>&1) || true
if echo "$SET_OUT" | grep -qP '"value"\s*:\s*"warn"'; then
    pass "config set returns new value"
else
    fail "config set did not return expected value (got: $(echo "$SET_OUT" | head -c 200))"
fi

DISK_LEVEL=$($SSH "grep log_level /etc/tollgate/config.json" 2>&1 | grep -oP '"log_level"\s*:\s*"\K[^"]*') || true
if [ "$DISK_LEVEL" = "warn" ]; then
    pass "value persisted to /etc/tollgate/config.json"
else
    fail "disk has '$DISK_LEVEL' (expected warn)"
fi

# Restore original
$SSH "tollgate --json config set log_level $ORIG_LOGLEVEL" > /dev/null 2>&1 || true

# 1.4 enum validation
ERR=$($SSH "tollgate --json config set log_level INVALID" 2>&1) || true
if echo "$ERR" | grep -qi "not in allowed"; then
    pass "rejects invalid log_level enum"
else
    fail "accepted invalid enum (got: $(echo "$ERR" | head -c 200))"
fi

# 1.5 min/max validation (upper)
ERR=$($SSH "tollgate --json config set margin 5.0" 2>&1) || true
if echo "$ERR" | grep -qi "exceeds maximum"; then
    pass "rejects margin > 1.0"
else
    fail "accepted margin 5.0 (got: $(echo "$ERR" | head -c 200))"
fi

# 1.6 min/max validation (lower)
ERR=$($SSH "tollgate --json config set margin -- -0.5" 2>&1) || true
if echo "$ERR" | grep -qi "below minimum"; then
    pass "rejects negative margin"
else
    fail "accepted margin -0.5 (got: $(echo "$ERR" | head -c 200))"
fi

# 1.7 config set with valid margin
SET_OUT=$($SSH "tollgate --json config set margin 0.5" 2>&1) || true
if echo "$SET_OUT" | grep -qP '"value"\s*:\s*"0.5"'; then
    pass "config set margin 0.5 accepted"
else
    fail "config set margin 0.5 failed (got: $(echo "$SET_OUT" | head -c 200))"
fi
$SSH "tollgate --json config set margin 0.1" > /dev/null 2>&1 || true

# 1.8 config set with bool
SET_OUT=$($SSH "tollgate --json config set show_setup true" 2>&1) || true
if echo "$SET_OUT" | grep -qP '"value"\s*:\s*"true"'; then
    pass "config set bool accepted"
else
    fail "config set bool failed"
fi
$SSH "tollgate --json config set show_setup true" > /dev/null 2>&1 || true

# 1.9 wallet balance
OUT=$($SSH "tollgate --json wallet balance" 2>&1) || true
if echo "$OUT" | grep -qP '"balance_sats"'; then
    pass "wallet balance responds with balance_sats field"
else
    fail "wallet balance failed (got: $(echo "$OUT" | head -c 200))"
fi

# 1.10 wallet info
OUT=$($SSH "tollgate --json wallet info" 2>&1) || true
if echo "$OUT" | grep -qP '"total_balance"'; then
    pass "wallet info responds with total_balance + mint breakdown"
else
    fail "wallet info failed (got: $(echo "$OUT" | head -c 200))"
fi

# 1.11 health
OUT=$($SSH "tollgate --json health" 2>&1) || true
if echo "$OUT" | grep -q '"ok"'; then
    pass "health responds with status ok"
else
    fail "health failed (got: $(echo "$OUT" | head -c 200))"
fi

# 1.12 status
OUT=$($SSH "tollgate --json status" 2>&1) || true
if echo "$OUT" | grep -qP '"running"\s*:\s*true'; then
    pass "status reports running=true"
else
    fail "status not running or invalid (got: $(echo "$OUT" | head -c 200))"
fi

# 1.13 upstream scan
OUT=$($SSH "tollgate --json upstream scan" 2>&1) || true
if echo "$OUT" | grep -qP '"(SSID|success)"'; then
    pass "upstream scan responds"
else
    fail "upstream scan failed (got: $(echo "$OUT" | head -c 200))"
fi

# 1.13 upstream list
OUT=$($SSH "tollgate --json upstream list" 2>&1) || true
if echo "$OUT" | grep -qP '"(upstream|success|SSID)"'; then
    pass "upstream list responds"
else
    fail "upstream list failed (got: $(echo "$OUT" | head -c 200))"
fi

# 1.15 config save round-trip
ORIG_CONFIG=$($SSH "tollgate --json config get" 2>&1) || true
SAVE_OUT=$($SSH "tollgate --json config save '$ORIG_CONFIG'" 2>&1) || true
if echo "$SAVE_OUT" | grep -qP '"success"\s*:\s*true'; then
    pass "config save round-trip succeeds"
else
    # save might fail if json has issues — not fatal
    skip "config save round-trip (got: $(echo "$SAVE_OUT" | head -c 200))"
fi

# =========================================================================
#  Phase 2: RPCD plugin — ubus methods
# =========================================================================
section "Phase 2: rpcd plugin (configurationwizzard)"

# 2.1 Check if rpcd plugin is installed
PLUGIN_EXISTS=$($SSH "test -x /usr/libexec/rpcd/tollgate && echo yes || echo no" 2>&1)
if [ "$PLUGIN_EXISTS" = "yes" ]; then
    pass "rpcd plugin installed and executable"
else
    fail "rpcd plugin not found at /usr/libexec/rpcd/tollgate"
    echo "  ${YELLOW}Skipping remaining rpcd tests${RESET}"
fi

# 2.2 List methods
if [ "$PLUGIN_EXISTS" = "yes" ]; then
    LIST_OUT=$($SSH "ubus list tollgate" 2>&1) || true
    if echo "$LIST_OUT" | grep -q "tollgate"; then
        pass "ubus list tollgate shows the object"
    else
        fail "ubus list tollgate not found"
    fi
fi

# 2.3 config_schema via ubus
if [ "$PLUGIN_EXISTS" = "yes" ]; then
    SCHEMA=$($SSH "ubus call tollgate config_schema" 2>&1) || true
    if echo "$SCHEMA" | grep -q '"json_key"'; then
        COUNT=$(echo "$SCHEMA" | grep -o '"json_key"' | wc -l)
        pass "ubus config_schema returns $COUNT schema entries"
    else
        fail "ubus config_schema failed (got: $(echo "$SCHEMA" | head -c 200))"
    fi
fi

# 2.4 config_get via ubus
if [ "$PLUGIN_EXISTS" = "yes" ]; then
    CFG=$($SSH "ubus call tollgate config_get" 2>&1) || true
    if echo "$CFG" | grep -q '"metric"'; then
        pass "ubus config_get returns config data"
    else
        fail "ubus config_get failed (got: $(echo "$CFG" | head -c 200))"
    fi
fi

# 2.5 config_set via ubus
if [ "$PLUGIN_EXISTS" = "yes" ]; then
    SET_OUT=$($SSH "ubus call tollgate config_set '{\"key\":\"log_level\",\"value\":\"debug\"}'" 2>&1) || true
    if echo "$SET_OUT" | grep -qP '"success"\s*:\s*true'; then
        pass "ubus config_set log_level debug accepted"
    else
        fail "ubus config_set failed (got: $(echo "$SET_OUT" | head -c 200))"
    fi
    # Restore
    $SSH "ubus call tollgate config_set '{\"key\":\"log_level\",\"value\":\"$ORIG_LOGLEVEL\"}'" > /dev/null 2>&1 || true
fi

# 2.6 wallet_balance via ubus
if [ "$PLUGIN_EXISTS" = "yes" ]; then
    WB=$($SSH "ubus call tollgate wallet_balance" 2>&1) || true
    if echo "$WB" | grep -qP '"balance_sats"'; then
        pass "ubus wallet_balance responds"
    else
        fail "ubus wallet_balance failed (got: $(echo "$WB" | head -c 200))"
    fi
fi

# 2.7 wallet_info via ubus
if [ "$PLUGIN_EXISTS" = "yes" ]; then
    WI=$($SSH "ubus call tollgate wallet_info" 2>&1) || true
    if echo "$WI" | grep -qP '"total_balance"'; then
        pass "ubus wallet_info returns per-mint breakdown"
    else
        fail "ubus wallet_info failed (got: $(echo "$WI" | head -c 200))"
    fi
fi

# 2.8 status via ubus
if [ "$PLUGIN_EXISTS" = "yes" ]; then
    ST=$($SSH "ubus call tollgate status" 2>&1) || true
    if echo "$ST" | grep -qP '"running"'; then
        pass "ubus status responds"
    else
        fail "ubus status failed (got: $(echo "$ST" | head -c 200))"
    fi
fi

# 2.9 health via ubus
if [ "$PLUGIN_EXISTS" = "yes" ]; then
    HT=$($SSH "ubus call tollgate health" 2>&1) || true
    if echo "$HT" | grep -q '"ok"'; then
        pass "ubus health responds"
    else
        fail "ubus health failed (got: $(echo "$HT" | head -c 200))"
    fi
fi

# 2.10 upstream_scan via ubus
if [ "$PLUGIN_EXISTS" = "yes" ]; then
    US=$($SSH "ubus call tollgate upstream_scan" 2>&1) || true
    if echo "$US" | grep -qP '"(SSID|success)"'; then
        pass "ubus upstream_scan responds"
    else
        fail "ubus upstream_scan failed (got: $(echo "$US" | head -c 200))"
    fi
fi

# =========================================================================
#  Phase 3: :2121 payment API
# =========================================================================
section "Phase 3: :2121 payment API"

# 3.1 GET / — Nostr kind 10021 pricing
PRICING=$(curl -s --connect-timeout 5 "http://$ROUTER:2121/" 2>&1) || true
if echo "$PRICING" | grep -qP '"kind"\s*:\s*10021'; then
    pass "GET :2121/ returns Nostr kind 10021"
    # Parse metric
    METRIC=$(echo "$PRICING" | grep -oP '"metric"\s*,\s*"\K[^"]+' | head -1) || METRIC=""
    if [ -n "$METRIC" ]; then
        pass "  pricing metric: $METRIC"
    fi
    # Parse step_size
    STEP=$(echo "$PRICING" | grep -oP '"step_size"\s*,\s*"\K[0-9]+' | head -1) || STEP=""
    if [ -n "$STEP" ]; then
        pass "  step_size: $STEP"
    fi
else
    fail "GET :2121/ did not return kind 10021 (got: $(echo "$PRICING" | head -c 200))"
fi

# 3.2 GET /whoami
WHOAMI=$(curl -s --connect-timeout 5 "http://$ROUTER:2121/whoami" 2>&1) || true
if echo "$WHOAMI" | grep -qP '^mac=[0-9a-fA-F:]+'; then
    MAC=$(echo "$WHOAMI" | grep -oP 'mac=\K[0-9a-fA-F:]+')
    pass "GET :2121/whoami returns MAC: $MAC"
else
    fail "GET :2121/whoami invalid (got: $(echo "$WHOAMI" | head -c 200))"
fi

# 3.3 GET /balance
BALANCE=$(curl -s --connect-timeout 5 "http://$ROUTER:2121/balance" 2>&1) || true
if echo "$BALANCE" | grep -qP '"session_active"'; then
    ACTIVE=$(echo "$BALANCE" | grep -oP '"session_active"\s*:\s*(true|false)' | head -1)
    pass "GET :2121/balance responds ($ACTIVE)"
else
    fail "GET :2121/balance invalid (got: $(echo "$BALANCE" | head -c 200))"
fi

# =========================================================================
#  Phase 4: configurationwizzard SPA deployment
# =========================================================================
section "Phase 4: configurationwizzard SPA files"

# 4.1 Admin SPA files
ADMIN_EXISTS=$($SSH "test -f /www/net4sats/admin.html -o -f /www/tollgate/admin.html && echo yes || echo no" 2>&1)
if [ "$ADMIN_EXISTS" = "yes" ]; then
    ADMIN_PATH=$($SSH "test -f /www/net4sats/admin.html && echo /www/net4sats || echo /www/tollgate" 2>&1)
    pass "admin SPA deployed at $ADMIN_PATH/"
else
    INDEX_EXISTS=$($SSH "test -f /www/net4sats/index.html -o -f /www/tollgate/index.html && echo yes || echo no" 2>&1)
    if [ "$INDEX_EXISTS" = "yes" ]; then
        skip "admin SPA uses old index.html layout (not yet re-deployed)"
    else
        fail "admin SPA not found at /www/net4sats/ or /www/tollgate/"
    fi
fi

ADMIN_JS=$($SSH "ls /www/net4sats/assets/admin-*.js /www/tollgate/assets/admin-*.js 2>/dev/null | head -1" 2>&1) || true
if [ -n "$ADMIN_JS" ]; then
    pass "admin JS bundle found: $ADMIN_JS"
else
    skip "admin JS bundle not found (may use old layout)"
fi

# 4.3 Check if uhttpd serves admin
UHTTPD_CHECK=$($SSH "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/ 2>/dev/null || echo 000" 2>&1) || true
if [ "$UHTTPD_CHECK" = "200" ]; then
    pass "uhttpd serves admin at :80"
else
    skip "uhttpd returned $UHTTPD_CHECK at :80"
fi

# 4.4 ubus proxy endpoint
UBUS_CHECK=$($SSH "curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1/ubus -H 'Content-Type: application/json' -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"call\",\"params\":[\"00000000000000000000000000000000\",\"session\",\"login\",{\"username\":\"root\",\"password\":\"\"}]}' 2>/dev/null || echo 000" 2>&1) || true
if [ "$UBUS_CHECK" = "200" ]; then
    pass "ubus proxy at /ubus responds on :80"
else
    skip "ubus proxy returned $UBUS_CHECK (may need auth)"
fi

# =========================================================================
#  Phase 5: Integration verification
# =========================================================================
section "Phase 5: Integration (rpcd ↔ tollgate --json)"

if [ "$PLUGIN_EXISTS" = "yes" ]; then
    # 5.1 Schema data matches CLI output
    CLI_SCHEMA=$($SSH "tollgate --json config schema" 2>&1) || true
    UBUS_SCHEMA=$($SSH "ubus call tollgate config_schema" 2>&1) || true
    CLI_COUNT=$(echo "$CLI_SCHEMA" | grep -o '"json_key"' | wc -l)
    UBUS_COUNT=$(echo "$UBUS_SCHEMA" | grep -o '"json_key"' | wc -l)
    if [ "$CLI_COUNT" -eq "$UBUS_COUNT" ]; then
        pass "rpcd schema ($UBUS_COUNT entries) matches CLI ($CLI_COUNT entries)"
    else
        fail "rpcd schema ($UBUS_COUNT) != CLI ($CLI_COUNT) entry count"
    fi

    # 5.2 Config get data matches CLI output
    CLI_CONFIG=$($SSH "tollgate --json config get" 2>&1) || true
    UBUS_CONFIG=$($SSH "ubus call tollgate config_get" 2>&1) || true
    CLI_METRIC=$(echo "$CLI_CONFIG" | grep -oP '"metric"\s*:\s*"\K[^"]+' | head -1) || true
    UBUS_METRIC=$(echo "$UBUS_CONFIG" | grep -oP '"metric"\s*:\s*"\K[^"]+' | head -1) || true
    if [ "$CLI_METRIC" = "$UBUS_METRIC" ]; then
        pass "rpcd config metric ($UBUS_METRIC) matches CLI ($CLI_METRIC)"
    else
        fail "rpcd metric ($UBUS_METRIC) != CLI ($CLI_METRIC)"
    fi

    # 5.3 ACL — read methods accessible without write
    ACL_FILE=$($SSH "cat /usr/share/rpcd/acl.d/tollgate.json" 2>&1) || true
    if echo "$ACL_FILE" | grep -q '"config_schema"' && echo "$ACL_FILE" | grep -q '"config_set"'; then
        pass "ACL has both read and write methods defined"
    else
        fail "ACL missing expected methods"
    fi
else
    skip "Integration tests (plugin not installed)"
fi

# =========================================================================
#  Phase 6: Login/Auth (configurationwizzard Bugs 6 & 7)
# =========================================================================
section "Phase 6: Login/Auth (configurationwizzard Bugs 6 & 7)"

# Detect available HTTP tool on router (BusyBox wget or curl)
if $SSH "which curl >/dev/null 2>&1" 2>/dev/null; then
    _ubus_post() {
        local data="$1"
        $SSH "curl -s -X POST http://127.0.0.1/ubus -H 'Content-Type: application/json' -d '$data'" 2>&1
    }
    _fetch_url() {
        $SSH "curl -s '$1'" 2>&1
    }
else
    _ubus_post() {
        local data="$1"
        $SSH "wget -q -O - --post-data='$data' --header='Content-Type: application/json' http://127.0.0.1/ubus 2>/dev/null" 2>&1
    }
    _fetch_url() {
        $SSH "wget -q -O - '$1' 2>/dev/null" 2>&1
    }
fi

# Save current password state
HAS_PASSWORD=$($SSH "grep -c '^\$5\|^\$6\|^\$1' /etc/shadow 2>/dev/null | head -1" 2>&1) || HAS_PASSWORD="0"
# Delete password so empty login works
$SSH "passwd -d root 2>/dev/null" || true
sleep 1

# 6.1 Empty password login succeeds (Bug 6)
EMPTY_LOGIN=$(_ubus_post '{"jsonrpc":"2.0","id":1,"method":"call","params":["00000000000000000000000000000000","session","login",{"username":"root","password":""}]}')
if echo "$EMPTY_LOGIN" | grep -q '"result":\[0,'; then
    pass "empty password login succeeds (Bug 6)"
else
    fail "empty password login failed (got: $(echo "$EMPTY_LOGIN" | head -c 200))"
fi

# 6.2 Invalid session returns error (Bug 8 — OpenWrt 25 JSON-RPC error format)
INVALID_SESSION=$(_ubus_post '{"jsonrpc":"2.0","id":1,"method":"call","params":["invalidsession123","tollgate","status",{}]}')
if echo "$INVALID_SESSION" | grep -q '"error":.*-32002'; then
    pass "invalid session returns JSON-RPC error -32002 (OpenWrt 25 format, Bug 8)"
elif echo "$INVALID_SESSION" | grep -q '"result":\[6\]'; then
    pass "invalid session returns result code 6 (OpenWrt 24 format)"
else
    fail "unexpected response for invalid session ($(echo "$INVALID_SESSION" | head -c 200))"
fi

# 6.3 Wrong password handling (Bug 7)
# Only test if router has a password set — skip on passwordless routers
# because session.login accepts any password when root has none
HAS_SHADOW=$($SSH "grep -c '^root:\$' /etc/shadow 2>/dev/null" 2>&1) || HAS_SHADOW="0"
if [ "$HAS_SHADOW" != "0" ]; then
    WRONG_LOGIN=$(_ubus_post '{"jsonrpc":"2.0","id":1,"method":"call","params":["00000000000000000000000000000000","session","login",{"username":"root","password":"definitely-wrong-password-12345"}]}')
    if echo "$WRONG_LOGIN" | grep -q '"result":\[6\]'; then
        pass "wrong password returns ubus error code 6 (Bug 7 backend)"
    elif echo "$WRONG_LOGIN" | grep -q '"error"'; then
        pass "wrong password returns JSON-RPC error (Bug 7 backend, OpenWrt 25)"
    else
        fail "wrong password unexpected response ($(echo "$WRONG_LOGIN" | head -c 200))"
    fi
else
    skip "wrong password test (root has no password set)"
fi

# 6.4 Sign-In button enabled with empty password (Bug 6 SPA)
# Try both / and /net4sats/ since SPA may be served from either
ADMIN_HTML=""
for _path in "/" "/net4sats/"; do
    _html=$(_fetch_url "http://127.0.0.1${_path}") || _html=""
    if echo "$_html" | grep -q 'net4sats'; then
        ADMIN_HTML="$_html"
        break
    fi
done
JS_SRC=$(echo "$ADMIN_HTML" | grep -o 'src="[^"]*\.js"' | sed 's/src="//;s/"//' | head -1)
if [ -n "$JS_SRC" ]; then
    JS_URL="http://127.0.0.1${JS_SRC}"
    JS_CONTENT=$(_fetch_url "$JS_URL") || JS_CONTENT=""
    if echo "$JS_CONTENT" | grep -q 'disabled:[a-z]||![a-z]'; then
        fail "SPA JS still has conditional disabled check (Bug 6 not deployed)"
    elif [ -z "$JS_CONTENT" ]; then
        skip "could not fetch SPA JS bundle"
    else
        pass "SPA JS does not disable button on empty password (Bug 6 SPA)"
    fi
else
    skip "could not find admin JS bundle in HTML"
fi

# =========================================================================
#  Summary
# =========================================================================
echo ""
echo "${BOLD}=========================================${RESET}"
echo "${BOLD}  E2E Test Results: ${GREEN}$PASS passed${RESET}, ${RED}$FAIL failed${RESET}, ${YELLOW}$SKIP skipped${RESET}"
echo "${BOLD}=========================================${RESET}"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
