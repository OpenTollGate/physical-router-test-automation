#!/usr/bin/env bash
set -euo pipefail

# Local Dry Test Runner — starts mock mint + Go backend, runs tests, cleans up.
#
# Usage:
#   ./scripts/local-test.sh                    # run all local tests
#   ./scripts/local-test.sh --keep-running     # don't stop services after tests
#   ./scripts/local-test.sh --debug            # verbose mint/backend logs + keep running
#   TOLLGATE_BACKEND_BINARY=/tmp/my-tollgate ./scripts/local-test.sh  # custom binary

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_SRC="${REPO_ROOT}/../tollgate-module-basic-go/src"
BACKEND_BIN="${TOLLGATE_BACKEND_BINARY:-/tmp/tollgate-test}"
MINT_PORT=3338
BACKEND_PORT=2121
LOOPBACK="${TOLLGATE_LOOPBACK:-127.0.0.1}"
KEEP_RUNNING=false
DEBUG=false

for arg in "$@"; do
  case "$arg" in
    --keep-running) KEEP_RUNNING=true ;;
    --debug)        DEBUG=true; KEEP_RUNNING=true ;;
  esac
done

echo "=== Local Dry Test Runner ==="
echo "Repo: $REPO_ROOT"
echo "Backend: $BACKEND_BIN"
echo "Mint: ${LOOPBACK}:${MINT_PORT}  Backend: ${LOOPBACK}:${BACKEND_PORT}"
[[ "$DEBUG" == "true" ]] && export MOCK_MINT_VERBOSE=1

# ─── 0. Kill stale processes on our ports ─────────────────────────
free_port() {
  local port=$1
  local pids
  pids=$(lsof -ti:$port 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "Port $port in use by PID(s) $pids — killing..."
    echo "$pids" | xargs kill -9 2>/dev/null || true
    sleep 1
  fi
}
free_port $MINT_PORT
free_port $BACKEND_PORT
free_port 5173

# ─── 1. Build backend (if needed) ────────────────────────────────
if [[ ! -f "$BACKEND_BIN" ]] && [[ -d "$BACKEND_SRC" ]]; then
    echo "Building Go backend..."
    (cd "$BACKEND_SRC" && go build -o "$BACKEND_BIN" .)
fi
if [[ ! -f "$BACKEND_BIN" ]]; then
    echo "ERROR: Backend binary not found at $BACKEND_BIN"
    exit 1
fi

# ─── 2. Create ndsctl stub ───────────────────────────────────────
STUB_DIR=$(mktemp -d /tmp/tollgate-stubs.XXXXXX)
cat > "$STUB_DIR/ndsctl" << 'NDSCTL'
#!/bin/bash
if [ "$1" = "auth" ]; then echo "Auth ok"; exit 0; fi
if [ "$1" = "deauth" ]; then echo "Deauth ok"; exit 0; fi
if [ "$1" = "json" ]; then echo '{"id":1,"state":"authenticated","downloaded":1048576,"uploaded":524288}'; exit 0; fi
echo "OK"; exit 0
NDSCTL
chmod +x "$STUB_DIR/ndsctl"

# ─── 2.5. Create uci stub ─────────────────────────────────────────
cat > "$STUB_DIR/uci" << 'UCI'
#!/bin/bash
# Simulate OpenWrt's uci config tool for testing on Ubuntu
if [ "$1" = "-q" ] && [ "$2" = "get" ]; then
    # uci -q get: simulate "not found" on Ubuntu
    exit 1
fi
if [ "$1" = "get" ]; then
    # uci get: simulate "not found" on Ubuntu
    exit 1
fi
if [ "$1" = "show" ]; then
    # uci show: return empty string
    echo ""
    exit 0
fi
# uci set, commit, add_list, delete, add: no-ops, succeed
if [ "$1" = "set" ] || [ "$1" = "commit" ] || [ "$1" = "add_list" ] || [ "$1" = "delete" ] || [ "$1" = "add" ]; then
    exit 0
fi
# Default: no-op success
exit 0
UCI
chmod +x "$STUB_DIR/uci"

# ─── 3. Create config ────────────────────────────────────────────
CONFIG_DIR=$(mktemp -d /tmp/tollgate-test-config.XXXXXX)
cat > "$CONFIG_DIR/config.json" << JSONEOF
{
  "config_version": "v0.0.8",
  "log_level": "info",
  "accepted_mints": [{
    "url": "http://$LOOPBACK:${MINT_PORT}",
    "min_balance": 0, "balance_tolerance_percent": 0,
    "payout_interval_seconds": 999999, "min_payout_amount": 999999,
    "price_per_step": 1, "price_unit": "sat", "purchase_min_steps": 1
  }],
  "step_size": 22020096, "margin": 0.1, "metric": "bytes",
  "show_setup": false, "reseller_mode": false, "redirect_url": "",
  "auth_delay_seconds": 0,
  "profit_share": [{"factor": 1.0, "identity": "owner"}],
  "upstream_detector": {"enabled": false},
  "upstream_session_manager": {"enabled": false},
  "upstream_wifi": {"enabled": false}
}
JSONEOF

echo "1700000000 1a:2b:3c:4d:5e:6f 127.0.0.1 test-client *" > /tmp/dhcp.leases

# ─── 4. Start mock mint ──────────────────────────────────────────
echo "Starting mock mint on $LOOPBACK:${MINT_PORT}..."
PYTHONUNBUFFERED=1 python3 "${REPO_ROOT}/lib/mock_mint.py" --port "$MINT_PORT" \
    > /tmp/mock-mint.log 2>&1 &
MINT_PID=$!

echo "Waiting for mock mint..."
for i in $(seq 1 10); do
    curl -sf --max-time 1 "http://$LOOPBACK:${MINT_PORT}/v1/info" > /dev/null 2>&1 && break
    sleep 0.5
done

# ─── 5. Start backend ────────────────────────────────────────────
echo "Starting backend on $LOOPBACK:${BACKEND_PORT}..."
TOLLGATE_TEST_CONFIG_DIR="$CONFIG_DIR" \
PATH="$STUB_DIR:$PATH" \
    "$BACKEND_BIN" > /tmp/tollgate-backend.log 2>&1 &
BACKEND_PID=$!

# ─── 6. Wait for health ──────────────────────────────────────────
# Backend serves HTTP 200 immediately, but returns kind=21023 (notice/degraded)
# until mint health probe completes. Must poll for kind=10021 (advertisement).
echo "Waiting for services..."
for i in $(seq 1 30); do
    curl -sf --max-time 2 "http://$LOOPBACK:${MINT_PORT}/v1/info" > /dev/null 2>&1 || { sleep 1; continue; }
    KIND=$(curl -sf --max-time 2 "http://$LOOPBACK:${BACKEND_PORT}/" 2>/dev/null \
        | python3 -c "import json,sys;print(json.load(sys.stdin).get('kind',0))" 2>/dev/null || echo "0")
    if [[ "$KIND" == "10021" ]]; then
        echo "Services ready! (kind=10021 advertisement)"
        break
    fi
    sleep 1
    [[ $i -eq 30 ]] && {
        echo "ERROR: Backend not ready after 30s (kind=$KIND)"
        cat /tmp/tollgate-backend.log | tail -10
        exit 1
    }
done

# ─── 7. Run tests ────────────────────────────────────────────────
echo ""
echo "=== Running tests ==="
cd "$REPO_ROOT"
TOLLGATE_SSH_HOST="" \
ROUTER_IP="" \
TOLLGATE_BACKEND_URL="http://$LOOPBACK:${BACKEND_PORT}" \
TOLLGATE_MINT_URL="http://$LOOPBACK:${MINT_PORT}" \
    python3 -m pytest tests/api/test_local_payment.py -v --tb=short 2>&1
TEST_EXIT=$?

if [[ $TEST_EXIT -ne 0 ]] || [[ "$DEBUG" == "true" ]]; then
    echo ""
    echo "=== Mock mint log (last 20 lines) ==="
    tail -20 /tmp/mock-mint.log 2>/dev/null || echo "(no log)"
    echo ""
    echo "=== Backend log (last 20 lines) ==="
    tail -20 /tmp/tollgate-backend.log 2>/dev/null || echo "(no log)"
    echo ""
    echo "=== Spent tokens ==="
    curl -sf "http://$LOOPBACK:${MINT_PORT}/test/spent" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "(unavailable)"
fi

# ─── 8. Cleanup ──────────────────────────────────────────────────
echo ""
if [[ "$KEEP_RUNNING" == "true" ]]; then
    echo "Services kept running (mint PID=$MINT_PID, backend PID=$BACKEND_PID)"
else
    echo "Cleaning up..."
    kill "$MINT_PID" "$BACKEND_PID" 2>/dev/null || true
    rm -rf "$CONFIG_DIR" "$STUB_DIR"
fi

exit $TEST_EXIT
