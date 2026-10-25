#!/usr/bin/env bash
# prta-bench-routes.sh — restore the x280 bench links after boot.
#
# Environment (from /etc/prta/bench.env):
#   E750_IFACE / E750_ADDR        default enx9c69d3ca3cfe / 192.168.8.244/24
#   MT3000_IFACE / MT3000_ADDR    default enx6c6e0750c33b / 10.110.223.2/24
#   COVR_HOST / COVR_IFACE / COVR_SRC
#   COVR_HOST is optional; a /32 route pins the COVR WAN management source.
set -euo pipefail

E750_IFACE="${E750_IFACE:-enx9c69d3ca3cfe}"
E750_ADDR="${E750_ADDR:-192.168.8.244/24}"
MT3000_IFACE="${MT3000_IFACE:-enx6c6e0750c33b}"
MT3000_ADDR="${MT3000_ADDR:-10.110.223.2/24}"
COVR_HOST="${COVR_HOST:-192.168.50.218}"
COVR_IFACE="${COVR_IFACE:-enx6c6e0750cd79}"
COVR_SRC="${COVR_SRC:-192.168.50.10}"

if [ -e "/sys/class/net/$E750_IFACE" ]; then
  ip link set "$E750_IFACE" up
  ip addr replace "$E750_ADDR" dev "$E750_IFACE"
fi
if [ -e "/sys/class/net/$MT3000_IFACE" ]; then
  ip link set "$MT3000_IFACE" up
  ip addr replace "$MT3000_ADDR" dev "$MT3000_IFACE"
fi
if [ -n "$COVR_HOST" ] && [ -e "/sys/class/net/$COVR_IFACE" ]; then
  ip route replace "$COVR_HOST/32" dev "$COVR_IFACE" src "$COVR_SRC"
fi
