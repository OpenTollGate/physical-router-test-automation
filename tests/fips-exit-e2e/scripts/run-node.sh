#!/usr/bin/env bash
# Run the raw FIPS Docker node as a test peer to VPS1
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

# Source env if exists
if [ -f .env ]; then
    set -a; source .env; set +a
fi

# Required env vars
: "${FIPS_NSEC:?Set FIPS_NSEC in .env or environment}"
: "${FIPS_NPUB:?Set FIPS_NPUB in .env or environment}"
: "${FIPS_PEER_NPUB:?Set FIPS_PEER_NPUB (VPS1 npub) in .env or environment}"

echo "=== Building fips-node image ==="
docker compose build fips-node 2>&1 | tail -3

echo ""
echo "=== Starting fips-node (peering with ${FIPS_PEER_ADDR:-66.92.204.38:2121}) ==="
docker compose up fips-node
