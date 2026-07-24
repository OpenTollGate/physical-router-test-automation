#!/bin/bash
# Entrypoint for exit-node container.
# Dual-homed: mesh (eth0) + public (eth1).
# Enables forwarding, adds MASQUERADE for mesh traffic exiting to public.
set -e

echo "[exit-node] Installing packages..."
apt-get update -qq && apt-get install -y -qq iptables iproute2 > /dev/null 2>&1

echo "[exit-node] Interfaces:"
ip addr show

# Enable IP forwarding
sysctl -w net.ipv4.ip_forward=1

# MASQUERADE: SNAT traffic from mesh network going out to public
# This makes return traffic come back through exit-node
iptables -t nat -A POSTROUTING -s 10.203.0.0/24 -j MASQUERADE

echo "[exit-node] NAT rules:"
iptables -t nat -L POSTROUTING -n -v
echo "[exit-node] Setup complete. Waiting..."
exec sleep infinity
