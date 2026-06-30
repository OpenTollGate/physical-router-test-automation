#!/bin/bash
# fips-cloud-lab worker — REAL MESH mode.
#
# Unlike worker.sh (which runs Docker chaos scenarios), this starts a real
# fips daemon that joins the public FIPS test mesh via Nostr discovery.
# Captures live mesh traffic, peer connections, and E2E session data.
#
# Configuration via GCP instance metadata:
#   fips-ref   — git ref to test
#   duration   — how long to capture after joining mesh (default: 120s)
set -eo pipefail

export HOME="${HOME:-/root}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$HOME/.cargo/bin"

meta() {
    curl -s -H "Metadata-Flavor: Google" \
        "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"
}

FIPS_REF="$(meta fips-ref)"
DURATION="$(meta duration)"
DURATION="${DURATION:-120}"
RUN_ID="$(meta run-id)"

RESULTS_DIR="/opt/fips-results"
WORK_DIR="/opt/fips"
mkdir -p "$RESULTS_DIR"

exec > >(tee -a "$RESULTS_DIR/worker.log") 2>&1

echo "=== fips-cloud-lab worker (REAL MESH) ==="
echo "  run-id:    $RUN_ID"
echo "  fips-ref:  $FIPS_REF"
echo "  duration:  ${DURATION}s"
echo "  started:   $(date -u +%Y-%m-%dT%H:%M:%SZ)"

fail() {
    echo "FAILED: $*" >&2
    echo "{ \"status\": \"failed\", \"error\": \"$*\" }" > "$RESULTS_DIR/FAILED"
    exit 1
}

# ── 1-4. Same setup as chaos worker (skip if baked) ─────────────────
if command -v docker &>/dev/null && command -v cargo &>/dev/null; then
    echo "[1/8] Baked image detected — skipping install"
else
    echo "[1/8] Installing system packages..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq \
        docker.io docker-compose-v2 python3 python3-pip \
        git curl build-essential pkg-config libclang-dev libdbus-1-dev \
        tcpdump iproute2 iperf3 \
        > /dev/null 2>&1
    systemctl start docker; systemctl enable docker > /dev/null 2>&1 || true
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable > /dev/null 2>&1
    source "$HOME/.cargo/env" 2>/dev/null || true
fi

echo "[2/8] Cloning fips at ref '$FIPS_REF'..."
rm -rf "$WORK_DIR"
git clone --quiet https://github.com/jmcorgan/fips.git "$WORK_DIR"
cd "$WORK_DIR"
git checkout "$FIPS_REF" 2>/dev/null || fail "ref '$FIPS_REF' not found"
echo "  HEAD: $(git rev-parse --short HEAD)"

echo "[3/8] Building fips..."
cargo build --release 2>&1 | tail -5
cp target/release/fips target/release/fipsctl target/release/fipstop . 2>/dev/null || true

echo "[4/8] Configuring fips for public mesh..."
mkdir -p /etc/fips
cp packaging/common/fips.yaml /etc/fips/fips.yaml
echo "  Config: $(wc -l < /etc/fips/fips.yaml) lines"

# ── 5. Start tcpdump ────────────────────────────────────────────────
echo "[5/8] Starting traffic capture..."
tcpdump -i eth0 -w "$RESULTS_DIR/mesh-traffic.pcap" -U not port 22 &
TCPDUMP_PID=$!
echo "  tcpdump PID: $TCPDUMP_PID"

# ── 6. Start fips daemon ────────────────────────────────────────────
echo "[6/8] Starting fips daemon (joining public mesh)..."
RUST_LOG=info ./target/release/fips --config /etc/fips/fips.yaml > "$RESULTS_DIR/fips-daemon.log" 2>&1 &
FIPS_PID=$!
echo "  fips PID: $FIPS_PID"
sleep 3

if ! kill -0 $FIPS_PID 2>/dev/null; then
    fail "fips daemon exited immediately. Check fips-daemon.log"
fi
echo "  daemon running"

# ── 7. Wait for mesh convergence + capture ───────────────────────────
echo "[7/8] Waiting for mesh convergence (up to 120s)..."

CONVERGED=false
for i in $(seq 1 24); do
    sleep 5
    PEER_COUNT=$(./target/release/fipsctl show peers 2>/dev/null | grep -c "npub\|peer\|connected" || echo "0")
    echo "  [${i}x5s] peers seen: $PEER_COUNT"
    if [ "$PEER_COUNT" -gt 0 ] 2>/dev/null; then
        CONVERGED=true
        echo "  PEERS FOUND — mesh convergence detected"
        break
    fi
done

echo ""
echo "  Capturing for ${DURATION}s..."

TIMESERIES_DIR="$RESULTS_DIR/timeseries"
mkdir -p "$TIMESERIES_DIR"

POLL=0
END_TIME=$(( $(date +%s) + DURATION ))
while [ $(date +%s) -lt $END_TIME ]; do
    TS=$(date -u +%Y%m%dT%H%M%SZ)
    POLL_DIR="$TIMESERIES_DIR/poll-$(printf '%04d' $POLL)-$TS"
    mkdir -p "$POLL_DIR"

    ./target/release/fipsctl show status > "$POLL_DIR/status.txt" 2>/dev/null || true
    ./target/release/fipsctl show peers > "$POLL_DIR/peers.txt" 2>/dev/null || true
    ./target/release/fipsctl show tree > "$POLL_DIR/tree.txt" 2>/dev/null || true
    ./target/release/fipsctl show sessions > "$POLL_DIR/sessions.txt" 2>/dev/null || true
    ./target/release/fipsctl show links > "$POLL_DIR/links.txt" 2>/dev/null || true
    ./target/release/fipsctl show stats > "$POLL_DIR/stats.txt" 2>/dev/null || true

    if [ $((POLL % 6)) -eq 0 ]; then
        echo "  [poll $POLL] $(date -u +%H:%M:%S) — still capturing"
    fi

    POLL=$((POLL + 1))
    sleep 5
done

# Try E2E: curl a .fips address if we have peers
echo ""
echo "  E2E: attempting curl over mesh..."
E2E_OUTPUT="$RESULTS_DIR/e2e-tests.txt"
{
    echo "=== fipsctl show status ==="
    ./target/release/fipsctl show status 2>&1 || true
    echo ""
    echo "=== fipsctl show peers ==="
    ./target/release/fipsctl show peers 2>&1 || true
    echo ""
    echo "=== fipsctl show sessions ==="
    ./target/release/fipsctl show sessions 2>&1 || true
    echo ""
    echo "=== ip addr show fips0 ==="
    ip addr show fips0 2>&1 || echo "(no fips0 interface)"
    echo ""
    echo "=== curl test: fips daemon health ==="
    curl -sf --max-time 10 http://[fd00::1]:8080/health 2>&1 || echo "(no HTTP service on mesh)"
} > "$E2E_OUTPUT" 2>&1

# ── 8. Collect results ──────────────────────────────────────────────
echo "[8/8] Collecting results..."
kill $FIPS_PID 2>/dev/null || true
kill $TCPDUMP_PID 2>/dev/null || true
wait $FIPS_PID 2>/dev/null || true
wait $TCPDUMP_PID 2>/dev/null || true

sleep 2

cp "$RESULTS_DIR/fips-daemon.log" "$RESULTS_DIR/" 2>/dev/null || true
echo "  timeseries polls: $(ls -d "$TIMESERIES_DIR"/poll-* 2>/dev/null | wc -l)"
echo "  pcap size: $(du -sh "$RESULTS_DIR/mesh-traffic.pcap" 2>/dev/null | cut -f1 || echo 'none')"
echo "  converged: $CONVERGED"

cat > "$RESULTS_DIR/DONE" <<EOF
{
  "status": "completed",
  "mode": "real-mesh",
  "run-id": "$RUN_ID",
  "fips-ref": "$FIPS_REF",
  "fips-commit": "$(git rev-parse HEAD)",
  "converged": $CONVERGED,
  "duration_secs": $DURATION,
  "polls": $POLL,
  "completed": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

echo "=== worker done ==="
