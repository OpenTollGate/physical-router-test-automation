#!/bin/bash
# =============================================================================
# TollGate Relay Valve Simulation — Test Runner
# =============================================================================
# Starts the Docker topology and runs 3 test phases:
#   1. Customer CANNOT reach internet-target (valve closed, no payment)
#   2. Customer CAN reach internet-target (valve open, after "payment")
#   3. Customer CANNOT reach internet-target (valve closed again, "expired")
#
# Usage:  ./run-tollgate-sim.sh
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.tollgate-sim.yml"
COMPOSE="docker compose -f ${COMPOSE_FILE}"

# Terminal colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'  # No Color
BOLD='\033[1m'

PASS_COUNT=0
FAIL_COUNT=0

# ---------------------------------------------------------------------------
# Helper: print a formatted test result line
# args: step_name expected(0=fail/1=pass) got(0=fail/1=pass)
# ---------------------------------------------------------------------------
print_result() {
    local step="$1" expect="$2" got="$3"

    local exp_str got_str result color
    if [ "$expect" = "1" ]; then exp_str="PASS"; else exp_str="FAIL"; fi
    if [ "$got"     = "1" ]; then got_str="PASS"; else got_str="FAIL"; fi

    if [ "$expect" = "$got" ]; then
        result="PASS"
        color="${GREEN}"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        result="FAIL"
        color="${RED}"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    # Pad step name to 40 chars for alignment
    printf "  ${BOLD}%-40s${NC} EXPECT: %-4s  GOT: %-4s  → ${color}${BOLD}%s${NC}\n" \
        "$step" "$exp_str" "$got_str" "$result"
}

# ---------------------------------------------------------------------------
# Helper: test if customer can reach internet-target
# returns 0 (success) if reachable, 1 (fail) if not
# ---------------------------------------------------------------------------
test_customer_reach() {
    # Try curl with a 4-second timeout. Non-zero exit = unreachable.
    if ${COMPOSE} exec -T customer-phone \
        curl -sf --connect-timeout 4 --max-time 6 http://203.0.113.100/ > /dev/null 2>&1; then
        return 0  # Reachable
    else
        return 1  # Unreachable
    fi
}

# ---------------------------------------------------------------------------
# Helper: wait for all containers to finish their setup (apt-get install etc.)
# ---------------------------------------------------------------------------
wait_for_ready() {
    echo -e "${CYAN}[*] Waiting for containers to finish setup...${NC}"

    local timeout=120 elapsed=0

    # Wait until tollgate has its iptables DROP rule in place
    while [ $elapsed -lt $timeout ]; do
        if ${COMPOSE} exec -T tollgate-phone \
            iptables -C FORWARD -s 10.203.0.11 -j DROP 2>/dev/null; then
            break
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done

    if [ $elapsed -ge $timeout ]; then
        echo -e "${RED}[!] Timeout waiting for tollgate to be ready${NC}"
        return 1
    fi
    echo -e "${GREEN}[✓] tollgate-phone ready (DROP rule active)${NC}"

    # Wait until exit-node has MASQUERADE in place
    elapsed=0
    while [ $elapsed -lt $timeout ]; do
        if ${COMPOSE} exec -T exit-node \
            iptables -t nat -C POSTROUTING -s 10.203.0.0/24 -j MASQUERADE 2>/dev/null; then
            break
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done

    if [ $elapsed -ge $timeout ]; then
        echo -e "${RED}[!] Timeout waiting for exit-node to be ready${NC}"
        return 1
    fi
    echo -e "${GREEN}[✓] exit-node ready (MASQUERADE active)${NC}"

    # Wait until customer has its default route set
    elapsed=0
    while [ $elapsed -lt $timeout ]; do
        if ${COMPOSE} exec -T customer-phone \
            ip route show default 2>/dev/null | grep -q "10.203.0.10"; then
            break
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done

    if [ $elapsed -ge $timeout ]; then
        echo -e "${RED}[!] Timeout waiting for customer to be ready${NC}"
        return 1
    fi
    echo -e "${GREEN}[✓] customer-phone ready (default route → 10.203.0.10)${NC}"

    # Wait until internet-target HTTP server is responding (from exit-node)
    elapsed=0
    while [ $elapsed -lt $timeout ]; do
        if ${COMPOSE} exec -T exit-node \
            curl -sf --connect-timeout 2 http://203.0.113.100/ > /dev/null 2>&1; then
            break
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done

    if [ $elapsed -ge $timeout ]; then
        echo -e "${RED}[!] Timeout waiting for internet-target HTTP server${NC}"
        return 1
    fi
    echo -e "${GREEN}[✓] internet-target ready (HTTP server on :80)${NC}"

    echo -e "${GREEN}[✓] All containers ready.${NC}"
    return 0
}

# ---------------------------------------------------------------------------
# Cleanup function
# ---------------------------------------------------------------------------
cleanup() {
    echo -e "${YELLOW}[*] Cleaning up...${NC}"
    ${COMPOSE} down --volumes --remove-orphans 2>/dev/null || true
}

# Trap to ensure cleanup on exit/error
trap cleanup EXIT

# ===========================================================================
# MAIN
# ===========================================================================
echo ""
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║     TollGate Relay Valve Simulation — Docker Compose         ║${NC}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# --- Step 0: Start topology ---
echo -e "${CYAN}[*] Starting topology...${NC}"
${COMPOSE} up -d --build 2>&1 | sed 's/^/    /'

echo ""

# Wait for readiness
if ! wait_for_ready; then
    echo -e "${RED}[!] Setup failed. Aborting.${NC}"
    exit 1
fi

echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  TEST RESULTS${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# --- Step 1: Customer blocked (no payment) ---
echo -e "${YELLOW}[*] STEP 1: Testing customer access BEFORE payment (valve should be CLOSED)...${NC}"
got=0
if test_customer_reach; then got=1; fi
print_result "Customer blocked (no payment)" 0 $got
echo ""

# --- Step 2: Simulate payment (remove DROP rule) ---
echo -e "${YELLOW}[*] STEP 2: Simulating Cashu payment received (opening valve)...${NC}"
${COMPOSE} exec -T tollgate-phone iptables -D FORWARD -s 10.203.0.11 -j DROP
echo -e "${GREEN}    → Valve OPENED (DROP rule removed)${NC}"

# Small delay for conntrack to settle
sleep 1

echo -e "${YELLOW}[*] Testing customer access AFTER payment (valve should be OPEN)...${NC}"
got=0
if test_customer_reach; then got=1; fi
print_result "Customer after payment" 1 $got
echo ""

# --- Step 3: Simulate payment expiry (re-add DROP rule) ---
echo -e "${YELLOW}[*] STEP 3: Simulating payment expiry (closing valve)...${NC}"
${COMPOSE} exec -T tollgate-phone iptables -A FORWARD -s 10.203.0.11 -j DROP
echo -e "${RED}    → Valve CLOSED (DROP rule re-added)${NC}"

# Flush conntrack so existing connections are torn down
${COMPOSE} exec -T tollgate-phone iptables -t nat -F 2>/dev/null || true
# Re-add since we just flushed nat (it was empty on tollgate anyway)
# Actually just clear conntrack on exit-node
${COMPOSE} exec -T exit-node bash -c 'command -v conntrack >/dev/null 2>&1 && conntrack -F || true' 2>/dev/null || true

# Small delay
sleep 1

echo -e "${YELLOW}[*] Testing customer access AFTER expiry (valve should be CLOSED)...${NC}"
got=0
if test_customer_reach; then got=1; fi
print_result "Customer after expiry" 0 $got
echo ""

# --- Summary ---
echo -e "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
if [ $FAIL_COUNT -eq 0 ]; then
    echo -e "${GREEN}${BOLD}  ALL TESTS PASSED (${PASS_COUNT}/${PASS_COUNT})${NC}"
    echo -e "${GREEN}  The TollGate relay valve concept is PROVEN.${NC}"
else
    echo -e "${RED}${BOLD}  SOME TESTS FAILED (PASS: ${PASS_COUNT}, FAIL: ${FAIL_COUNT})${NC}"
fi
echo -e "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
echo ""

exit $FAIL_COUNT
