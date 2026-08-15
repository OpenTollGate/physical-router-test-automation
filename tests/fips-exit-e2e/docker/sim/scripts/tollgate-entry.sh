#!/bin/bash
# Entrypoint for tollgate-phone container.
# The "payment valve": blocks customer traffic by default.
# A Cashu payment removes the block; payment expiry re-adds it.
set -e

echo "[tollgate] Installing packages..."
apt-get update -qq && apt-get install -y -qq iptables iproute2 > /dev/null 2>&1

echo "[tollgate] Interfaces:"
ip addr show

# Enable IP forwarding (we relay between customer and exit)
sysctl -w net.ipv4.ip_forward=1

# Replace Docker's default gateway with exit-node as our upstream
ip route del default 2>/dev/null || true
ip route add default via 10.203.0.20

# === THE VALVE ===
# Block all forwarded traffic from customer (10.203.0.11) by default.
# This simulates "no payment received yet".
iptables -A FORWARD -s 10.203.0.11 -j DROP

echo "[tollgate] FORWARD rules (initial — valve CLOSED):"
iptables -L FORWARD -n -v
echo "[tollgate] Setup complete. Valve CLOSED (customer blocked)."
exec sleep infinity
