#!/usr/bin/env python3
"""
publish-nostr-announce.py

Publishes a Nostr kind 30078 route-announce event for a TollGate/FIPS exit node.
Designed to run as a cron job on VPS1.

Uses pynostr (already installed on VPS1).
"""
import json
import os
import sys
import time

from pynostr.event import Event
from pynostr.key import PrivateKey
from pynostr.relay_manager import RelayManager

# --- Config ---
RELAYS = os.environ.get("FIPS_RELAYS", "wss://relay1.orangesync.tech,wss://nos.lol").split(",")
EXIT_ADDR = os.environ.get("FIPS_EXIT_ADDR", "66.92.204.38")
EXIT_UDP_PORT = int(os.environ.get("FIPS_EXIT_UDP_PORT", "2121"))
EXIT_TCP_PORT = int(os.environ.get("FIPS_EXIT_TCP_PORT", "8443"))

# Get nsec
nsec = os.environ.get("FIPS_NSEC")
if not nsec:
    nsec_file = os.environ.get("FIPS_NSEC_FILE", "/etc/fips/fips.nsec")
    try:
        with open(nsec_file) as f:
            nsec = f.read().strip()
    except FileNotFoundError:
        print(f"ERROR: Set FIPS_NSEC env var or create {nsec_file}", file=sys.stderr)
        sys.exit(1)

# Also publish a TollGate HTTP gateway URL for the app's Nostr discovery
# The app queries for kind 30078 with d="tollgate-gateway"
HTTP_GATEWAY = os.environ.get("TOLLGATE_HTTP_URL", f"http://{EXIT_ADDR}:4747")

# --- Build event ---
pk = PrivateKey.from_nsec(nsec)
npub = pk.public_key.bech32()

content = json.dumps({
    "http": HTTP_GATEWAY,
    "npub": npub,
    "addr": EXIT_ADDR,
    "udp_port": EXIT_UDP_PORT,
    "tcp_port": EXIT_TCP_PORT,
    "transport": "fips+tollgate-v2",
    "timestamp": int(time.time()),
})

event = Event(
    kind=30078,
    content=content,
    created_at=int(time.time()),
    tags=[
        ["d", "tollgate-gateway"],
        ["t", "tollgate"],
        ["t", "fips-exit"],
        ["addr", f"{EXIT_ADDR}:{EXIT_UDP_PORT}"],
        ["transport", "udp"],
    ],
)
event.sign(pk.hex())

# --- Publish ---
rm = RelayManager()
for relay in RELAYS:
    rm.add_relay(relay.strip())
rm.open_connections()
time.sleep(1)

rm.publish_event(event.to_dict())
print(f"Published kind 30078 tollgate-gateway announcement:")
print(f"  npub:     {npub}")
print(f"  http:     {HTTP_GATEWAY}")
print(f"  fips:     {EXIT_ADDR}:{EXIT_UDP_PORT} (udp) / :{EXIT_TCP_PORT} (tcp)")
print(f"  event id: {event.id}")

time.sleep(2)
rm.close_connections()
print("Done.")
