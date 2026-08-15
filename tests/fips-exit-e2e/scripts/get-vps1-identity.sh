#!/bin/bash
# Get VPS1 FIPS identity (npub) without quoting issues
set -euo pipefail

PASS="$(grep '^VPS_PASSWORD=' /home/c03rad0r/tollgate-infrastructure-kit/.env | head -1 | cut -d= -f2 | tr -d '\"')"

echo "=== FIPS config (nsec hidden) ==="
sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 debian@66.92.204.38 'sudo cat /etc/fips/fips.yaml' 2>/dev/null

echo ""
echo "=== FIPS node journal npub ==="
sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 debian@66.92.204.38 'sudo journalctl -u fips --no-pager -n 100 2>/dev/null | grep -Eo "npub1[a-z0-9]+" | head -5'

echo ""
echo "=== FIPS version ==="
sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 debian@66.92.204.38 'fips --version 2>/dev/null || echo "no version flag"'
