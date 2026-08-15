#!/bin/bash
# Build FIPS v0.4.0 from source, copy binary to fips-exit-e2e
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FIPS_REPO="$SCRIPT_DIR/../../fips"
E2E_DIR="$SCRIPT_DIR/.."

echo "=== Checking out FIPS v0.4.0 (pinned, pre-refactor) ==="
cd "$FIPS_REPO"
git checkout v0.4.0 2>/dev/null || echo "Already on v0.4.0"

echo ""
echo "=== Building fips daemon ==="
cargo build --release -p fips 2>&1 | tail -3

echo ""
echo "=== Copying binaries to fips-exit-e2e ==="
cp target/release/fips "$E2E_DIR/fips" && echo "✅ fips ($(./target/release/fips --version))"
cp target/release/fipsctl "$E2E_DIR/fipsctl" 2>/dev/null && echo "✅ fipsctl"
cp target/release/fipstop "$E2E_DIR/fipstop" 2>/dev/null && echo "✅ fipstop"

echo ""
echo "=== Rebuilding Docker image ==="
cd "$E2E_DIR"
docker build -t fips-node -f docker/Dockerfile.fips-node . 2>&1 | tail -2
echo "✅ Docker image rebuilt with FIPS v0.4.0"
