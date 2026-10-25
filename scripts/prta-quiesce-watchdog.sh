#!/usr/bin/env bash
# prta-quiesce-watchdog.sh — disable APs when no test holds the bench lease.
#
# Installed as a systemd timer on x280 (see deploy/x280/install-bench.sh).
# A failed test therefore cannot leave a TollGate/private SSID beaconing.
set -euo pipefail

ROOT="${PRTA_ROOT:-/home/c08r4d0r/src/prta-main}"
cd "$ROOT"

if scripts/hw-bench-lease status 2>/dev/null | grep -q "bench free"; then
  python3 scripts/quiesce-router.py --all --allow-unlocked >/dev/null 2>&1 || true
fi
