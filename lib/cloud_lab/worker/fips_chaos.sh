#!/bin/bash
# fips-cloud-lab worker — runs ON the GCP VM as a startup script.
#
# Installs Docker + Rust, clones fips at a ref, builds the test image,
# runs one chaos scenario, and writes a DONE/FAILED marker so the
# submit.py poll loop can collect results and tear the VM down.
#
# Configuration arrives via GCP instance metadata:
#   fips-ref   — git ref to test (branch, tag, or commit)
#   scenario   — chaos scenario name (e.g. smoke-10, churn-20)
#
# This script is intentionally self-contained and idempotent — it
# is the only thing that runs on the VM, and the VM is ephemeral.
set -eo pipefail  # no -u: GCP startup scripts don't set HOME

export HOME="${HOME:-/root}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$HOME/.cargo/bin"

# ── Metadata fetch helper ───────────────────────────────────────────
meta() {
    curl -s -H "Metadata-Flavor: Google" \
        "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"
}

FIPS_REF="$(meta fips-ref)"
SCENARIO="$(meta scenario)"
RUN_ID="$(meta run-id)"

RESULTS_DIR="/opt/fips-results"
WORK_DIR="/opt/fips"
mkdir -p "$RESULTS_DIR"

# Log everything to worker.log (collected with the results)
exec > >(tee -a "$RESULTS_DIR/worker.log") 2>&1

echo "=== fips-cloud-lab worker ==="
echo "  run-id:    $RUN_ID"
echo "  fips-ref:  $FIPS_REF"
echo "  scenario:  $SCENARIO"
echo "  started:   $(date -u +%Y-%m-%dT%H:%M:%SZ)"

fail() {
    echo "FAILED: $*" >&2
    echo "{ \"status\": \"failed\", \"error\": \"$*\", \"time\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" }" \
        > "$RESULTS_DIR/FAILED"
    exit 1
}

# ── 1. Install system dependencies ──────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo "[1/7] Installing system packages..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq \
        docker.io docker-compose-v2 \
        python3 python3-pip python3-venv \
        git curl build-essential \
        pkg-config libclang-dev libdbus-1-dev \
        tcpdump iproute2 \
        > /dev/null 2>&1
    systemctl start docker
    systemctl enable docker > /dev/null 2>&1 || true
else
    echo "[1/7] Docker already present — skipping install (baked image)"
fi

# ── 2. Install Rust toolchain ───────────────────────────────────────
if ! command -v cargo &>/dev/null; then
    echo "[2/7] Installing Rust..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --default-toolchain stable > /dev/null 2>&1
fi
source "$HOME/.cargo/env" 2>/dev/null || true

# ── 3. Install Python deps for the chaos harness ────────────────────
python3 -c "import yaml, jinja2" 2>/dev/null || {
    echo "[3/7] Installing Python dependencies (pyyaml, jinja2)..."
    pip3 install -q pyyaml jinja2 > /dev/null 2>&1 \
        || pip3 install --break-system-packages -q pyyaml jinja2 > /dev/null 2>&1
}

# ── 4. Clone fips at the requested ref ──────────────────────────────
echo "[4/6] Cloning fips at ref '$FIPS_REF'..."
rm -rf "$WORK_DIR"
git clone --quiet https://github.com/jmcorgan/fips.git "$WORK_DIR"
cd "$WORK_DIR"
git checkout "$FIPS_REF" 2>/dev/null || fail "ref '$FIPS_REF' not found"
echo "  HEAD: $(git rev-parse --short HEAD) — $(git log -1 --format='%s')"

# ── 5. Build binaries + Docker test image ───────────────────────────
echo "[5/6] Building fips + Docker test image (this takes a few minutes)..."
if ! ./testing/scripts/build.sh; then
    fail "testing/scripts/build.sh exited non-zero"
fi
echo "  fips binary: $(file testing/docker/fips | cut -d: -f2-)"

# ── 6. Run the chaos scenario with live data capture ────────────────
echo "[6/7] Running chaos scenario '$SCENARIO' with live capture..."
SCENARIO_FILE="testing/chaos/scenarios/${SCENARIO}.yaml"
if [ ! -f "$SCENARIO_FILE" ]; then
    fail "scenario '$SCENARIO' not found at $SCENARIO_FILE"
fi

TIMESERIES_DIR="$RESULTS_DIR/timeseries"
PCAP_DIR="$RESULTS_DIR/pcaps"
mkdir -p "$TIMESERIES_DIR" "$PCAP_DIR"

# Sidecar: poll running containers for live metrics while the scenario runs.
poll_loop() {
    local poll_count=0
    local containers=""

    echo "[poll] Waiting for fips containers..."
    while true; do
        containers=$(docker ps --filter name=fips-node- --format '{{.Names}}' 2>/dev/null | sort)
        [ -n "$containers" ] && break
        sleep 2
    done
    echo "[poll] Found containers: $(echo $containers | tr '\n' ' ')"

    for c in $containers; do
        docker exec -d "$c" tcpdump -i eth0 -w "/tmp/${c}.pcap" -U not port 22 2>/dev/null || true
    done

    while true; do
        local ts; ts=$(date -u +%Y%m%dT%H%M%SZ)
        local poll_dir="$TIMESERIES_DIR/poll-$(printf '%04d' $poll_count)-${ts}"
        mkdir -p "$poll_dir"

        for c in $containers; do
            docker exec "$c" fipsctl show tree > "$poll_dir/${c}-tree.txt" 2>/dev/null &
            docker exec "$c" fipsctl show peers > "$poll_dir/${c}-peers.txt" 2>/dev/null &
            docker exec "$c" fipsctl show stats > "$poll_dir/${c}-stats.txt" 2>/dev/null &
        done
        wait

        local running; running=$(docker ps --filter name=fips-node- --format '{{.Names}}' 2>/dev/null | wc -l)
        if [ "$running" -eq 0 ] 2>/dev/null; then
            echo "[poll] Containers torn down, stopping after $poll_count polls."
            break
        fi

        poll_count=$((poll_count + 1))
        sleep 5
    done

    # Containers may be gone by the time we reach here — copy pcaps while they exist
    for c in $containers; do
        docker cp "$c:/tmp/${c}.pcap" "$PCAP_DIR/${c}.pcap" 2>/dev/null || true
    done
    echo "[poll] Captured $poll_count time-series polls + $(ls "$PCAP_DIR"/*.pcap 2>/dev/null | wc -l) pcaps."
}

cd testing/chaos

poll_loop &
POLL_PID=$!
echo "  poller started (PID $POLL_PID)"

if ! python3 -m sim "scenarios/${SCENARIO}.yaml"; then
    rc=$?
    echo "  scenario exited with code $rc (2=panics, 3=assertions)"
    echo "$rc" > "$RESULTS_DIR/exit-code"
fi

kill "$POLL_PID" 2>/dev/null || true
wait "$POLL_PID" 2>/dev/null || true

# ── 7. Collect results ──────────────────────────────────────────────
echo "[7/7] Collecting results..."
cp -r sim-results/* "$RESULTS_DIR/" 2>/dev/null || true

if [ -f "$RESULTS_DIR/analysis.txt" ]; then
    echo "  analysis.txt: $(wc -l < "$RESULTS_DIR/analysis.txt") lines"
fi
echo "  timeseries polls: $(ls -d "$TIMESERIES_DIR"/poll-* 2>/dev/null | wc -l)"
echo "  pcap files: $(ls "$PCAP_DIR"/*.pcap 2>/dev/null | wc -l)"

cat > "$RESULTS_DIR/DONE" <<EOF
{
  "status": "completed",
  "run-id": "$RUN_ID",
  "fips-ref": "$FIPS_REF",
  "fips-commit": "$(git rev-parse HEAD)",
  "scenario": "$SCENARIO",
  "completed": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

echo "=== worker done ==="
