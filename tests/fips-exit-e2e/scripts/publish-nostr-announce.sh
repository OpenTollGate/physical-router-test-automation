#!/bin/bash
# publish-nostr-announce.sh
# Publishes a Nostr kind 30078 route-announce event for a FIPS exit node.
#
# Designed to run as a cron job on the exit node VPS. Requires the FIPS
# node's nsec (environment variable $FIPS_NSEC or file $FIPS_NSEC_FILE).
#
# Usage:
#   export FIPS_NSEC=nsec1...
#   ./publish-nostr-announce.sh
#
#   # Or with a file:
#   export FIPS_NSEC_FILE=/etc/fips/fips.nsec
#   ./publish-nostr-announce.sh
#
# Relays: set $FIPS_RELAYS (space-separated) or uses defaults.

set -euo pipefail

# --- Config ---
# Relays to publish to (default: tollgate project relays)
RELAYS="${FIPS_RELAYS:-wss://relay1.orangesync.tech wss://nos.lol}"

# Exit node parameters (override via env)
EXIT_ADDR="${FIPS_EXIT_ADDR:-66.92.204.38}"
EXIT_UDP_PORT="${FIPS_EXIT_UDP_PORT:-2121}"
EXIT_TCP_PORT="${FIPS_EXIT_TCP_PORT:-8443}"
EXIT_NPUB="${FIPS_EXIT_NPUB:-npub1569mplttzhmxuxxktduj8uwsn0g77ky3c34598sxclpjxkfndfksm4p6gp}"

# --- Get the nsec ---
if [ -n "${FIPS_NSEC:-}" ]; then
    NSEC="$FIPS_NSEC"
elif [ -n "${FIPS_NSEC_FILE:-}" ] && [ -f "$FIPS_NSEC_FILE" ]; then
    NSEC=$(cat "$FIPS_NSEC_FILE" | tr -d '[:space:]')
else
    echo "ERROR: FIPS_NSEC or FIPS_NSEC_FILE must be set"
    exit 1
fi

# --- Build the event JSON ---
CONTENT=$(cat <<JSON
{
  "addr": "${EXIT_ADDR}",
  "udp_port": ${EXIT_UDP_PORT},
  "tcp_port": ${EXIT_TCP_PORT},
  "npub": "${EXIT_NPUB}",
  "protocol": "fips",
  "transport": "udp+tcp",
  "version": "0.4.0",
  "timestamp": $(date +%s)
}
JSON
)

# Remove newlines for the content field
CONTENT_FLAT=$(echo "$CONTENT" | tr -d '\n')

TIMESTAMP=$(date +%s)

# Create the event as JSON for nak
EVENT_JSON=$(cat <<JSON
{
  "kind": 30078,
  "content": ${CONTENT_FLAT@Q},
  "created_at": ${TIMESTAMP},
  "tags": [
    ["d", "tollgate-exit-node"],
    ["t", "fips-exit"],
    ["t", "tollgate"],
    ["n", "${EXIT_NPUB}"],
    ["addr", "${EXIT_ADDR}:${EXIT_UDP_PORT}"],
    ["transport", "udp"]
  ]
}
JSON
)

# --- Publish ---
echo "Publishing kind 30078 route-announce to relays..."
echo "  exit npub: ${EXIT_NPUB}"
echo "  addr:      ${EXIT_ADDR}:${EXIT_UDP_PORT} (udp) / :${EXIT_TCP_PORT} (tcp)"
echo "  relays:    ${RELAYS}"
echo ""

echo "$EVENT_JSON" | nak event --sec "$NSEC" $RELAYS 2>&1

echo ""
echo "Done."
