#!/usr/bin/env bash
set -euo pipefail

# Local Dry Test Runner — starts mock mint + Go backend, runs tests, cleans up.
#
# Usage:
#   ./scripts/local-test.sh                    # run all local tests
#   ./scripts/local-test.sh --keep-running     # don't stop services after tests
#   TOLLGATE_BACKEND_BINARY=/tmp/my-tollgate ./scripts/local-test.sh  # custom binary

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_SRC="${REPO_ROOT}/../tollgate-module-basic-go/src"
BACKEND_BIN="${TOLLGATE_BACKEND_BINARY:-/tmp/tollgate-test}"
MINT_PORT=3338
BACKEND_PORT=2121
LOOPBACK="${TOLLGATE_LOOPBACK:-127.0.0.1}"
KEEP_RUNNING=false

[[ "${1:-}" == "--keep-running" ]] && KEEP_RUNNING=true

echo "=== Local Dry Test Runner ==="
echo "Repo: $REPO_ROOT"

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

# ─── 3. Create config ────────────────────────────────────────────
CONFIG_DIR=$(mktemp -d /tmp/tollgate-test-config.XXXXXX)
cat > "$CONFIG_DIR/config.json" << JSONEOF
{
  "config_version": "1",
  "log_level": "info",
  "accepted_mints": [{
    "url": "http://$LOOPBACK:${MINT_PORT}",
    "min_balance": 0, "balance_tolerance_percent": 0,
    "payout_interval_seconds": 999999, "min_payout_amount": 999999,
    "price_per_step": 1, "price_unit": "sats", "purchase_min_steps": 1
  }],
  "step_size": 22020096, "margin": 0.1, "metric": "bytes",
  "show_setup": false, "reseller_mode": false, "redirect_url": "",
  "auth_delay_seconds": 0,
  "upstream_detector": {"enabled": false},
  "upstream_session_manager": {"enabled": false},
  "upstream_wifi": {"enabled": false}
}
JSONEOF

echo "1700000000 1a:2b:3c:4d:5e:6f ::1 test-client *" > /tmp/dhcp.leases

# ─── 4. Start mock mint ──────────────────────────────────────────
echo "Starting mock mint on $LOOPBACK:${MINT_PORT}..."
PYTHONUNBUFFERED=1 python3 "${REPO_ROOT}/lib/mock_mint.py" --port "$MINT_PORT" \
    > /tmp/mock-mint.log 2>&1 &
MINT_PID=$!

# ─── 5. Start backend ────────────────────────────────────────────
echo "Starting backend on $LOOPBACK:${BACKEND_PORT}..."
TOLLGATE_TEST_CONFIG_DIR="$CONFIG_DIR" \
PATH="$STUB_DIR:$PATH" \
    "$BACKEND_BIN" > /tmp/tollgate-backend.log 2>&1 &
BACKEND_PID=$!

# ─── 6. Wait for health ──────────────────────────────────────────
echo "Waiting for services..."
for i in $(seq 1 20); do
    if curl -sf --max-time 2 "http://$LOOPBACK:${MINT_PORT}/v1/info" > /dev/null 2>&1 && \
       curl -sf --max-time 2 "http://$LOOPBACK:${BACKEND_PORT}/" > /dev/null 2>&1; then
        echo "Services ready!"
        break
    fi
    sleep 1
    [[ $i -eq 20 ]] && { echo "ERROR: Services didn't start in time"; exit 1; }
done

# Verify merchant is ready
KIND=$(curl -sf --max-time 3 "http://$LOOPBACK:${BACKEND_PORT}/" | python3 -c "import json,sys;print(json.load(sys.stdin).get('kind',0))" 2>/dev/null || echo "0")
if [[ "$KIND" != "10021" ]]; then
    echo "ERROR: Backend not ready (kind=$KIND)"
    cat /tmp/tollgate-backend.log | tail -10
    exit 1
fi

# ─── 7. Run tests ────────────────────────────────────────────────
echo ""
echo "=== Running tests ==="
cd "$REPO_ROOT"
TOLLGATE_BACKEND_URL="http://$LOOPBACK:${BACKEND_PORT}" \
TOLLGATE_MINT_URL="http://$LOOPBACK:${MINT_PORT}" \
    python3 -m pytest tests/api/test_local_payment.py -v --tb=short 2>&1
TEST_EXIT=$?

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
