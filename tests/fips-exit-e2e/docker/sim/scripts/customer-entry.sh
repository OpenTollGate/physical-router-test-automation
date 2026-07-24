#!/bin/bash
# Entrypoint for customer-phone container.
# Routes all traffic through tollgate-phone (10.203.0.10).
# Before payment: curl to internet-target fails (tollgate drops it).
# After payment:  curl to internet-target succeeds (tollgate allows it).
set -e

echo "[customer] Installing packages..."
apt-get update -qq && apt-get install -y -qq curl iproute2 iputils-ping > /dev/null 2>&1

echo "[customer] Interfaces:"
ip addr show

# Route all non-mesh traffic through tollgate-phone
ip route del default 2>/dev/null || true
ip route add default via 10.203.0.10

echo "[customer] Routing table:"
ip route show
echo "[customer] Setup complete. Default gateway → 10.203.0.10 (tollgate-phone)"
exec sleep infinity
