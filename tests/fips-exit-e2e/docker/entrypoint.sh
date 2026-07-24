#!/bin/bash
# Entrypoint for raw FIPS daemon container.
set -e

CONFIG="/etc/fips/fips.yaml"
NSEC="${FIPS_NSEC:?FIPS_NSEC is required}"
UDP_BIND="${FIPS_UDP_BIND:-0.0.0.0:2121}"
TCP_BIND="${FIPS_TCP_BIND:-0.0.0.0:8443}"
TUN_MTU="${FIPS_TUN_MTU:-1280}"

# IMPORTANT: FIPS v0.5.0-dev expects tun/dns/transports at YAML ROOT level,
# NOT nested under node:. The node: section only contains identity.
cat > "$CONFIG" <<YAML
node:
  identity:
    nsec: "${NSEC}"
  discovery:
    nostr:
      enabled: true
      policy: configured_only
      app: "fips-overlay-v1"
      advertise: false

tun:
  enabled: true
  name: fips0
  mtu: ${TUN_MTU}

dns:
  enabled: true
  bind_addr: "127.0.0.1"

transports:
  udp:
    bind_addr: "${UDP_BIND}"
    advertise_on_nostr: true
    mtu: 1472
  tcp:
    bind_addr: "${TCP_BIND}"
    advertise_on_nostr: true
YAML

if [ -n "$FIPS_EXTERNAL_ADDR" ]; then
    cat >> "$CONFIG" <<YAML
    external_addr: "${FIPS_EXTERNAL_ADDR}"
YAML
fi

if [ -n "$FIPS_PEER_NPUB" ] && [ -n "$FIPS_PEER_ADDR" ]; then
    cat >> "$CONFIG" <<YAML
peers:
  - npub: "${FIPS_PEER_NPUB}"
    alias: "vps1-exit"
    addresses:
      - transport: udp
        addr: "${FIPS_PEER_ADDR}"
    connect_policy: auto_connect
YAML
fi

echo "=== Generated config ==="
grep -v 'nsec:' "$CONFIG"
exec /usr/local/bin/fips --config "$CONFIG"
