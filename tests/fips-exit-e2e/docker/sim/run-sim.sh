#!/bin/bash
# TollGate Relay Valve Simulation — Phase 1
# Proves: middle node gates internet access based on "payment" status.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE="docker compose -f ${SCRIPT_DIR}/docker-compose.tollgate-sim.yml"
PROJECT="tollgate-sim"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS=0
FAIL=0

result() {
    local name="$1" expect="$2" got="$3"
    if [ "$expect" = "$got" ]; then
        echo -e "  ${GREEN}✓ PASS${NC} — $name (expected: $expect, got: $got)"
        PASS=$((PASS+1))
    else
        echo -e "  ${RED}✗ FAIL${NC} — $name (expected: $expect, got: $got)"
        FAIL=$((FAIL+1))
    fi
}

echo "=== TollGate Relay Valve Simulation ==="
echo ""

# Cleanup any previous run
$COMPOSE down -v --remove-orphans 2>/dev/null || true

echo "[1/5] Building images..."
$COMPOSE build --quiet 2>&1 | tail -1

echo "[2/5] Starting topology..."
$COMPOSE up -d 2>&1 | grep -E "Started|Error" || true

echo "[3/5] Waiting for containers..."
sleep 5

# Verify containers are running
RUNNING=$($COMPOSE ps --format '{{.Name}} {{.Status}}' 2>/dev/null | wc -l)
if [ "$RUNNING" -lt 4 ]; then
    echo "ERROR: Only $RUNNING/4 containers running"
    $COMPOSE logs 2>/dev/null | tail -20
    exit 1
fi
echo "  All 4 containers running."

echo ""
echo "[4/5] Running tests..."
echo ""

# Test 1: Customer BLOCKED (valve closed, no payment)
echo "TEST 1: Customer cannot reach internet (no payment)"
$COMPOSE exec -T customer-phone curl -sf --connect-timeout 3 http://203.0.113.100/ >/dev/null 2>&1
RESULT1=$?
if [ $RESULT1 -ne 0 ]; then
    result "Customer blocked without payment" "BLOCKED" "BLOCKED"
else
    result "Customer blocked without payment" "BLOCKED" "REACHED (BUG!)"
fi

# Simulate payment: remove DROP rule on tollgate
echo ""
echo "--- Simulating Cashu payment received ---"
$COMPOSE exec -T tollgate-phone iptables -D FORWARD -s 10.203.0.11 -j DROP 2>/dev/null
echo "  Valve OPENED (DROP rule removed)"

# Test 2: Customer CAN reach internet (valve open, payment received)
echo ""
echo "TEST 2: Customer can reach internet (after payment)"
$COMPOSE exec -T customer-phone curl -sf --connect-timeout 5 http://203.0.113.100/ >/dev/null 2>&1
RESULT2=$?
if [ $RESULT2 -eq 0 ]; then
    result "Customer reaches internet after payment" "REACHED" "REACHED"
else
    result "Customer reaches internet after payment" "REACHED" "BLOCKED (BUG!)"
fi

# Simulate payment expiry: re-add DROP rule
echo ""
echo "--- Simulating payment expiry ---"
$COMPOSE exec -T tollgate-phone iptables -A FORWARD -s 10.203.0.11 -j DROP 2>/dev/null
echo "  Valve CLOSED (DROP rule re-added)"

# Test 3: Customer BLOCKED again (payment expired)
echo ""
echo "TEST 3: Customer cannot reach internet (payment expired)"
$COMPOSE exec -T customer-phone curl -sf --connect-timeout 3 http://203.0.113.100/ >/dev/null 2>&1
RESULT3=$?
if [ $RESULT3 -ne 0 ]; then
    result "Customer blocked after expiry" "BLOCKED" "BLOCKED"
else
    result "Customer blocked after expiry" "BLOCKED" "REACHED (BUG!)"
fi

echo ""
echo "[5/5] Cleanup..."
$COMPOSE down -v --remove-orphans 2>/dev/null || true

echo ""
echo "================================"
echo -e "  RESULTS: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}"
echo "================================"
[ $FAIL -eq 0 ] && exit 0 || exit 1
