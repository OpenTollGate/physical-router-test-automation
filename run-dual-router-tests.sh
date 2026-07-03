#!/bin/bash
# run-dual-router-tests.sh — Run net4sats tests against BOTH routers
#
# Router 1: GL-MT6000 (Flint 3) at 192.168.1.1 — OpenWrt 25.12, apk
# Router 2: GL-MT3000/CF-WR632AX at 10.47.41.1 — OpenWrt 24.10, opkg
#
# Usage: bash run-dual-router-tests.sh
set -euo pipefail

ROUTERS=("192.168.1.1" "10.47.41.1")
ROUTER_NAMES=("GL-MT6000" "GL-MT3000")
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESULTS_DIR="${SCRIPT_DIR}/results/dual-router-$(date +%Y%m%d-%H%M%S)"
mkdir -p "${RESULTS_DIR}"

echo "═══════════════════════════════════════════════════════════════"
echo "  net4sats Dual-Router Test Suite"
echo "  Results: ${RESULTS_DIR}"
echo "═══════════════════════════════════════════════════════════════"

# ── Phase 1: API/SSH tests per router ──
test_router_api() {
    local ip="$1"
    local name="$2"
    local outdir="${RESULTS_DIR}/${name}"
    mkdir -p "${outdir}"

    echo ""
    echo "── ${name} (${ip}) — API + SSH Tests ──"
    local pass=0
    local fail=0
    local skip=0
    local results=""

    run_test() {
        local test_name="$1"
        local test_cmd="$2"
        local desc="$3"
        local result
        result=$(eval "${test_cmd}" 2>&1) && {
            results+="PASS|${test_name}|${desc}|$(echo "${result}" | head -3)\n"
            echo "  [PASS] ${test_name}"
            ((pass++))
        } || {
            results+="FAIL|${test_name}|${desc}|$(echo "${result}" | head -3)\n"
            echo "  [FAIL] ${test_name}: $(echo "${result}" | tail -1)"
            ((fail++))
        }
    }

    skip_test() {
        local test_name="$1"
        local reason="$2"
        results+="SKIP|${test_name}|${reason}|\n"
        echo "  [SKIP] ${test_name}: ${reason}"
        ((skip++))
    }

    # T1: SSH accessible
    run_test "ssh-access" \
        "ssh -o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=5 root@${ip} 'echo OK'" \
        "SSH root login works"

    # T2: Router info
    local info
    info=$(ssh -o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=5 root@${ip} \
        "cat /etc/openwrt_release 2>/dev/null | grep DISTRIB_DESCRIPTION; uname -r" 2>/dev/null)
    results+="INFO|system|${info}|\n"
    echo "  [INFO] ${info}"

    # T3: tollgate-wrt running
    run_test "tollgate-process" \
        "ssh -o BatchMode=yes root@${ip} 'pgrep -a tollgate-wrt'" \
        "tollgate-wrt binary is running"

    # T4: Tollgate API health (port 2121)
    run_test "api-health-2121" \
        "curl -sf -m 5 http://${ip}:2121/ -o /dev/null -w '%{http_code}'" \
        "Port 2121 API responds"

    # T5: API returns JSON status
    local api_response
    api_response=$(curl -sf -m 5 "http://${ip}:2121/" 2>/dev/null || echo "")
    if echo "${api_response}" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
        results+="PASS|api-json|Returns valid JSON|$(echo "${api_response}" | head -3)\n"
        echo "  [PASS] api-json"
        ((pass++))
    else
        results+="FAIL|api-json|Expected JSON, got: ${api_response:0:100}|\n"
        echo "  [FAIL] api-json: not valid JSON"
        ((fail++))
    fi

    # T6: HTTP port 80 (LuCI/admin)
    run_test "http-80" \
        "curl -sf -m 5 -o /dev/null -w '%{http_code}' http://${ip}/" \
        "Port 80 web UI responds"

    # T7: net4sats portal site exists
    run_test "net4sats-portal" \
        "ssh -o BatchMode=yes root@${ip} 'ls /www/net4sats/ 2>/dev/null | head -1'" \
        "/www/net4sats/ directory exists"

    # T8: net4sats portal accessible
    run_test "net4sats-http" \
        "curl -sf -m 5 -o /dev/null -w '%{http_code}' http://${ip}/net4sats/" \
        "net4sats portal page loads"

    # T9: Tollgate config present
    run_test "tollgate-config" \
        "ssh -o BatchMode=yes root@${ip} 'test -f /etc/tollgate/config.json && echo OK'" \
        "/etc/tollgate/config.json exists"

    # T10: WiFi AP active
    local wifi_ap
    wifi_ap=$(ssh -o BatchMode=yes root@${ip} "iwinfo 2>/dev/null | grep -c 'Mode: Master'" 2>/dev/null || echo "0")
    if [ "${wifi_ap}" -ge 1 ] 2>/dev/null; then
        results+="PASS|wifi-ap|${wifi_ap} AP interface(s) active|\n"
        echo "  [PASS] wifi-ap (${wifi_ap} APs)"
        ((pass++))
    else
        results+="FAIL|wifi-ap|No AP interfaces active|\n"
        echo "  [FAIL] wifi-ap: no AP interfaces"
        ((fail++))
    fi

    # T11: WiFi STA (upstream client) — only test router should have this
    local wifi_sta
    wifi_sta=$(ssh -o BatchMode=yes root@${ip} "iwinfo 2>/dev/null | grep -c 'Mode: Client'" 2>/dev/null || echo "0")
    if [ "${wifi_sta}" -ge 1 ] 2>/dev/null; then
        results+="PASS|wifi-sta|${wifi_sta} STA interface(s) active|\n"
        echo "  [PASS] wifi-sta (${wifi_sta} STA upstream)"
        ((pass++))
    else
        skip_test "wifi-sta" "No STA mode on this router"
    fi

    # T12: Port 2121 balance endpoint
    run_test "api-balance" \
        "curl -sf -m 5 'http://${ip}:2121/balance'" \
        "API /balance endpoint responds"

    # T13: Port 2121 usage endpoint
    run_test "api-usage" \
        "curl -sf -m 5 'http://${ip}:2121/usage'" \
        "API /usage endpoint responds"

    # T14: SSL/TLS support
    local ssl_dir
    ssl_dir=$(ssh -o BatchMode=yes root@${ip} "ls /etc/tollgate/ssl/ 2>/dev/null" || echo "")
    if [ -n "${ssl_dir}" ]; then
        results+="PASS|ssl-certs|SSL dir has: ${ssl_dir}|\n"
        echo "  [PASS] ssl-certs (${ssl_dir})"
        ((pass++))
    else
        skip_test "ssl-certs" "No SSL directory"
    fi

    # T15: Disk space healthy
    local disk_pct
    disk_pct=$(ssh -o BatchMode=yes root@${ip} "df / | tail -1 | awk '{print \$5}' | tr -d '%'" 2>/dev/null || echo "100")
    if [ "${disk_pct}" -lt 90 ] 2>/dev/null; then
        results+="PASS|disk-space|${disk_pct}% used|\n"
        echo "  [PASS] disk-space (${disk_pct}% used)"
        ((pass++))
    else
        results+="FAIL|disk-space|${disk_pct}% used|\n"
        echo "  [FAIL] disk-space (${disk_pct}% used)"
        ((fail++))
    fi

    # Write results
    echo -e "${results}" > "${outdir}/api-results.txt"
    echo ""
    echo "  ${name} API Summary: ${pass} pass, ${fail} fail, ${skip} skip"

    # Return pass/fail counts
    echo "${pass}:${fail}:${skip}"
}

# ── Phase 2: Playwright browser tests per router ──
test_router_playwright() {
    local ip="$1"
    local name="$2"
    local outdir="${RESULTS_DIR}/${name}/playwright"
    mkdir -p "${outdir}"

    echo ""
    echo "── ${name} (${ip}) — Playwright Browser Tests ──"

    cd "${SCRIPT_DIR}/tests"

    # Run net4sats captive portal test
    ROUTER_IP="${ip}" npx playwright test \
        browser/net4sats-captive-portal.spec.mjs \
        --reporter=list \
        --output="${outdir}" \
        --timeout=60000 \
        2>&1 | tee "${outdir}/playwright-output.txt" || true

    # Extract pass/fail from output
    local pw_pass
    pw_pass=$(grep -c 'passed' "${outdir}/playwright-output.txt" 2>/dev/null || echo "0")
    local pw_fail
    pw_fail=$(grep -cE 'failed|Error' "${outdir}/playwright-output.txt" 2>/dev/null || echo "0")

    echo "  ${name} Playwright: see ${outdir}/"
}

# ── Run all tests ──
ALL_RESULTS=""

for i in "${!ROUTERS[@]}"; do
    ip="${ROUTERS[$i]}"
    name="${ROUTER_NAMES[$i]}"

    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  Testing ${name} at ${ip}"
    echo "═══════════════════════════════════════════════════════════════"

    # Phase 1: API tests
    api_result=$(test_router_api "${ip}" "${name}")
    ALL_RESULTS="${ALL_RESULTS}${name}|${api_result}\n"

    # Phase 2: Playwright tests
    test_router_playwright "${ip}" "${name}"
done

# ── Summary ──
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  DUAL-ROUTER TEST SUMMARY"
echo "═══════════════════════════════════════════════════════════════"
echo -e "${ALL_RESULTS}" | while IFS='|' read name counts; do
    if [ -n "${name}" ] && [ -n "${counts}" ]; then
        pass=$(echo "${counts}" | cut -d: -f1)
        fail=$(echo "${counts}" | cut -d: -f2)
        skip=$(echo "${counts}" | cut -d: -f3)
        echo "  ${name}: ${pass} pass, ${fail} fail, ${skip} skip"
    fi
done
echo ""
echo "  Full results: ${RESULTS_DIR}"
echo "═══════════════════════════════════════════════════════════════"
