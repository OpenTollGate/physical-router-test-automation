#!/bin/bash
# Entrypoint for internet-target container.
# Runs a simple HTTP server on port 80, reachable only via the exit-node.
set -e

echo "[target] Installing packages..."
apt-get update -qq && apt-get install -y -qq python3 > /dev/null 2>&1

echo "[target] Starting HTTP server on :80..."
cd /var/www
exec python3 -m http.server 80 --bind 0.0.0.0
