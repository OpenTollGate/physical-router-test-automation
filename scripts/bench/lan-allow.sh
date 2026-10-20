#!/usr/bin/env bash
# lan-allow.sh — give a bench host IP access to a TollGate router's management
# plane (SSH :22, the module API :2121, the NDS portal :2050) over the router's
# WAN, so the PRTA specs can drive a serial-only fleet router by IP.
#
# WHY this exists: a TollGate router keeps its management plane on br-lan /
# br-private only. The module's `backend_input_firewall` chain (a separate
# nft chain at `priority filter - 1`) drops :2121 from anything that is not
# lo/br-lan/br-private, and the fw4 input policy rejects :22 from the WAN. The
# fleet serial routers (MT3000 on t440, MT6000 on x280) have no LAN path into
# the fleet, so without this the specs cannot reach them.
#
# CAVEAT: these are AD-HOC nft rules. A backend restart re-applies the module
# firewall and WIPES them — re-run this right before a spec run (the run's own
# `router.restart_backend()` may also drop them mid-run; keep it last).
#
# Usage:
#   scripts/bench/lan-allow.sh <router-ip> <bench-host-ip> [password]
#   scripts/bench/lan-allow.sh --revert <router-ip> <bench-host-ip> [password]
#
# Then run the specs:
#   TOLLGATE_SSH_HOST=<router-ip> TOLLGATE_SSH_PASSWORD=<pw> \
#     pytest tests/api tests/scenarios -v
set -euo pipefail

REVERT=0
if [ "${1:-}" = "--revert" ]; then REVERT=1; shift; fi
ROUTER="${1:?router IP required}"
BENCH="${2:?bench-host IP required}"
PW="${3:-TollGate}"

SSH=(sshpass -p "$PW" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
     -o LogLevel=ERROR -o ConnectTimeout=8 "root@$ROUTER")

if [ "$REVERT" -eq 1 ]; then
  # Delete our tagged rules by handle from both the fw4 input chain and the
  # module's backend_input_firewall chain.
  "${SSH[@]}" 'for ch in input backend_input_firewall; do
      for h in $(nft -a list chain inet fw4 $ch 2>/dev/null | grep -E "bench-(allow|tmp)" | grep -oE "handle [0-9]+" | awk "{print \$2}"); do
        nft delete rule inet fw4 $ch handle $h 2>/dev/null || true
      done
    done; echo "remaining bench rules: $(nft list ruleset 2>/dev/null | grep -cE "bench-(allow|tmp)")"'
  exit 0
fi

# fw4 input: SSH + portal + the API from the bench host (the API accept here is
# belt-and-braces; the module chain below is what actually gates :2121).
"${SSH[@]}" "nft insert rule inet fw4 input ip saddr $BENCH tcp dport { 22, 2121, 2050, 80, 443 } accept comment \"bench-allow\" 2>/dev/null; \
nft insert rule inet fw4 backend_input_firewall ip saddr $BENCH tcp dport 2121 accept comment \"bench-allow\" 2>/dev/null; \
echo allowed"

echo "allowed $BENCH -> $ROUTER:{22,2121,2050}"
