#!/bin/bash
# Run raw FIPS Docker node and verify connection to VPS1
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

set -a; source .env; set +a

echo "=== Starting raw FIPS node peering with VPS1 ==="
docker compose run --rm --name fips-test-node fips-node 2>&1 &

FIPS_PID=$!
echo "FIPS daemon PID: $FIPS_PID"
sleep 8

echo ""
echo "=== Check if container is running ==="
docker ps --filter "name=fips-test-node" --format "{{.Names}} {{.Status}}" 2>/dev/null || echo "Container not found"

echo ""
echo "=== Container logs (last 20) ==="
docker logs fips-test-node 2>/dev/null | tail -20 || echo "No logs yet"

echo ""
echo "=== Test connectivity: nc to VPS1:2121 ==="
docker exec fips-test-node timeout 5 bash -c "echo 'test' | nc -u -w 2 66.92.204.38 2121" 2>&1 && echo "UDP reachable" || echo "UDP test done"

# Cleanup
sleep 5
echo ""
echo "=== Full logs ==="
docker logs fips-test-node 2>/dev/null | tail -30
docker rm -f fips-test-node 2>/dev/null || true