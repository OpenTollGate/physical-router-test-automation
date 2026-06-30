#!/bin/bash
# fips-cloud-lab worker — INTEROP mode.
#
# Builds three fips versions and runs the mixed-version interop harness.
# Tests wire-compatibility between release candidates and prior releases.
#
# Metadata:
#   ref-a  — version under test (e.g. v0.4.0-rc2)
#   ref-b  — comparison ref (e.g. master)
#   ref-c  — release baseline (e.g. v0.3.0)
#   spec   — node-spec (default: "a a b c")
set -eo pipefail

export HOME="${HOME:-/root}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$HOME/.cargo/bin"

meta() { curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"; }

REF_A="$(meta ref-a)"
REF_B="$(meta ref-b)"
REF_C="$(meta ref-c)"
SPEC="$(meta spec)"
SPEC="${SPEC:-a a b c}"
RUN_ID="$(meta run-id)"

REF_A="${REF_A:-v0.4.0-rc2}"
REF_B="${REF_B:-master}"
REF_C="${REF_C:-v0.3.0}"

RESULTS_DIR="/opt/fips-results"
WORK_DIR="/opt/fips"
mkdir -p "$RESULTS_DIR"
exec > >(tee -a "$RESULTS_DIR/worker.log") 2>&1

echo "=== fips-cloud-lab worker (INTEROP) ==="
echo "  run-id:  $RUN_ID"
echo "  ref-a:   $REF_A (version under test)"
echo "  ref-b:   $REF_B (comparison)"
echo "  ref-c:   $REF_C (release baseline)"
echo "  spec:    $SPEC"
echo "  started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

fail() { echo "FAILED: $*" >&2; echo "{\"status\":\"failed\",\"error\":\"$*\"}" > "$RESULTS_DIR/FAILED"; exit 1; }

# ── Install (skip if baked) ─────────────────────────────────────────
if ! command -v cargo &>/dev/null; then
    echo "[1/5] Installing build deps..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -y -qq docker.io docker-compose-v2 python3 python3-pip git curl build-essential pkg-config libclang-dev libdbus-1-dev tcpdump > /dev/null 2>&1
    systemctl start docker; systemctl enable docker > /dev/null 2>&1 || true
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable > /dev/null 2>&1
    source "$HOME/.cargo/env" 2>/dev/null || true
    pip3 install -q pyyaml jinja2 > /dev/null 2>&1 || pip3 install --break-system-packages -q pyyaml jinja2 > /dev/null 2>&1
else
    echo "[1/5] Baked image — skipping install"
fi

# ── Clone fips ──────────────────────────────────────────────────────
echo "[2/5] Cloning fips..."
rm -rf "$WORK_DIR"
git clone --quiet https://github.com/jmcorgan/fips.git "$WORK_DIR"
cd "$WORK_DIR"

# ── Build interop images ────────────────────────────────────────────
echo "[3/5] Building interop images (3 cargo builds, ~30min)..."
export FIPS_INTEROP_RUNS_DIR="/opt/fips-interop"
mkdir -p "$FIPS_INTEROP_RUNS_DIR"

if ! bash testing/interop/build-images.sh "$REF_A" "$REF_B" "$REF_C"; then
    fail "build-images.sh failed"
fi

echo "  Images built:"
cat "$FIPS_INTEROP_RUNS_DIR/.build/refs.env" 2>/dev/null || cat testing/interop/.build/refs.env 2>/dev/null

# ── Run interop test ────────────────────────────────────────────────
echo "[4/5] Running interop test (spec: $SPEC)..."
cd testing/interop

if ! bash interop-test.sh $SPEC; then
    rc=$?
    echo "  interop-test.sh exited $rc — collecting diagnostics"
    echo "$rc" > "$RESULTS_DIR/exit-code"
fi

# ── Collect results ─────────────────────────────────────────────────
echo "[5/5] Collecting results..."
cp -r "$FIPS_INTEROP_RUNS_DIR/.build/refs.env" "$RESULTS_DIR/refs.env" 2>/dev/null || true
cp -r "$FIPS_INTEROP_RUNS_DIR/generated-configs/"* "$RESULTS_DIR/" 2>/dev/null || true
cp -r "$FIPS_INTEROP_RUNS_DIR/.stress-runs/"* "$RESULTS_DIR/" 2>/dev/null || true

# Also copy from default location if FIPS_INTEROP_RUNS_DIR wasn't used
cp -r testing/interop/.build/refs.env "$RESULTS_DIR/refs.env" 2>/dev/null || true
cp -r testing/interop/generated-configs/* "$RESULTS_DIR/" 2>/dev/null || true
cp -r testing/interop/.stress-runs/* "$RESULTS_DIR/" 2>/dev/null || true

# Collect container logs
for c in $(docker ps -a --filter name=fips-interop --format '{{.Names}}' 2>/dev/null); do
    docker logs "$c" > "$RESULTS_DIR/container-${c}.log" 2>&1 || true
done

cat > "$RESULTS_DIR/DONE" <<EOF
{
  "status": "completed",
  "mode": "interop",
  "run-id": "$RUN_ID",
  "ref-a": "$REF_A",
  "ref-b": "$REF_B",
  "ref-c": "$REF_C",
  "spec": "$SPEC",
  "completed": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

echo "=== worker done ==="
