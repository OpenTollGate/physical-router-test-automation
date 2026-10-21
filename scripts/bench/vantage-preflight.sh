#!/usr/bin/env bash
# vantage-preflight.sh — prove the TESTER is a stranger BEFORE recording any
# captive-portal / detection / gating result.
#
# Why this exists (measured 2026-10-21): a review of the `gatewaydomainname`
# regression measured a redirect chain from a host whose own wlan MAC was
# `state=Authenticated` on the portal. An authenticated (or trusted) client is
# BYPASSED by design — no HTTP interception — so every reading taken from it is
# meaningless as customer evidence, and the resulting claim had to be retracted.
#
# The kit already *mentions* this in club-acceptance.sh, but only as an
# informational line. This makes it a hard, exit-coded preflight that any portal
# lane can call first.
#
# Usage:
#   scripts/bench/vantage-preflight.sh                 # probe host's own wlan MAC
#   scripts/bench/vantage-preflight.sh <mac>           # check a specific MAC
#   scripts/bench/vantage-preflight.sh --json <mac>    # machine-readable
#
# Exit codes:
#   0  vantage OK — the MAC is Preauthenticated (a normal stranger)
#   10 vantage BAD — the MAC is Authenticated (bypassed: results invalid)
#   11 vantage BAD — the MAC is Trusted (exempt by design: results invalid)
#   12 cannot determine — unreadable client list is NOT a pass (fail closed)
#   13 a trustedmac exemption is configured for the probing MAC
#   2  usage / missing prerequisites
set -uo pipefail

ROUTER_IP="${ROUTER_IP:-192.168.1.1}"
ROUTER_PASS="${ROUTER_PASS:-test123}"
JSON=0
WANT_MAC=""

for arg in "$@"; do
  case "$arg" in
    --json) JSON=1 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) WANT_MAC="$arg" ;;
  esac
done

command -v sshpass >/dev/null 2>&1 || { echo "vantage-preflight: sshpass not found" >&2; exit 2; }

rsh() {
  timeout 25 sshpass -p "$ROUTER_PASS" ssh -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=8 -o LogLevel=ERROR "root@$ROUTER_IP" "$@" 2>/dev/null
}

# --- which MAC are we testing? -------------------------------------------------
# Default: the probing host's own wlan MAC. Deliberately NOT the wired NIC: the
# portal sees the address that reaches it, and for a wireless client that is the
# wlan MAC. If neither is determinable, we cannot prove vantage -> exit 12.
if [ -z "$WANT_MAC" ]; then
  WANT_MAC="$(cat /sys/class/net/wlp*/address 2>/dev/null | head -1)"
  [ -n "$WANT_MAC" ] || WANT_MAC="$(cat /sys/class/net/wlan*/address 2>/dev/null | head -1)"
fi
if [ -z "$WANT_MAC" ]; then
  if [ "$JSON" = 1 ]; then
    printf '{"mac":"","verdict":"unknown","reason":"no_probing_nic"}\n'
  else
    echo "vantage-preflight: cannot determine the probing MAC (no wlp*/wlan* NIC); pass one explicitly" >&2
  fi
  exit 12
fi

# --- 1. trustedmac exemption (checked FIRST: it is the silent killer) ----------
TRUSTED_RAW="$(rsh 'uci show nodogsplash 2>/dev/null | grep -i trustedmac')"
if [ -z "$TRUSTED_RAW" ]; then
  # An unreadable config is not "no exemptions" — fail closed.
  if ! rsh 'test -f /etc/config/nodogsplash'; then
    echo "vantage-preflight: cannot read nodogsplash config (unreachable router?)" >&2
    exit 12
  fi
fi
if printf '%s' "$TRUSTED_RAW" | grep -qi "$WANT_MAC"; then
  [ "$JSON" = 1 ] && printf '{"mac":"%s","verdict":"bad","reason":"trustedmac_exemption"}\n' "$WANT_MAC"
  [ "$JSON" = 0 ] && echo "VANTAGE BAD: $WANT_MAC is in nodogsplash trustedmac — exempt by design, results invalid"
  exit 13
fi

# --- 2. client state from ndsctl ----------------------------------------------
CLIENTS="$(rsh 'ndsctl clients 2>/dev/null')"
if [ -z "$CLIENTS" ]; then
  if [ "$JSON" = 1 ]; then
    printf '{"mac":"%s","verdict":"unknown","reason":"unreadable_client_list"}\n' "$WANT_MAC"
  else
    echo "vantage-preflight: ndsctl clients returned nothing — an unreadable client list is NOT a pass" >&2
  fi
  exit 12
fi

STATE="$(printf '%s\n' "$CLIENTS" | awk -v m="$WANT_MAC" '
  $0 ~ ("mac=" m) {found=1; next}
  found && /^state=/ {sub(/^state=/,""); print; exit}
')"

if [ -z "$STATE" ]; then
  # Not listed at all: the router has never seen this MAC. That is a *valid*
  # stranger vantage for a first-contact test, but it means no state exists yet.
  [ "$JSON" = 1 ] && printf '{"mac":"%s","verdict":"ok","reason":"not_yet_seen","state":"absent"}\n' "$WANT_MAC"
  [ "$JSON" = 0 ] && echo "VANTAGE OK: $WANT_MAC not yet seen by the router (first-contact vantage)"
  exit 0
fi

case "$STATE" in
  Preauthenticated)
    [ "$JSON" = 1 ] && printf '{"mac":"%s","verdict":"ok","reason":"preauthenticated","state":"%s"}\n' "$WANT_MAC" "$STATE"
    [ "$JSON" = 0 ] && echo "VANTAGE OK: $WANT_MAC is Preauthenticated (a normal stranger)"
    exit 0 ;;
  Authenticated)
    [ "$JSON" = 1 ] && printf '{"mac":"%s","verdict":"bad","reason":"authenticated","state":"%s"}\n' "$WANT_MAC" "$STATE"
    [ "$JSON" = 0 ] && cat <<EOF
VANTAGE BAD: $WANT_MAC is Authenticated — this client is BYPASSED by the portal.
No redirect chain, captive-detection or gating reading taken from it is valid.
Fix: deauth it, or measure from a MAC the box has never seen:
  ndsctl deauth $WANT_MAC
EOF
    exit 10 ;;
  Trusted)
    [ "$JSON" = 1 ] && printf '{"mac":"%s","verdict":"bad","reason":"trusted","state":"%s"}\n' "$WANT_MAC" "$STATE"
    [ "$JSON" = 0 ] && echo "VANTAGE BAD: $WANT_MAC is Trusted — exempt by design, results invalid"
    exit 11 ;;
  *)
    [ "$JSON" = 1 ] && printf '{"mac":"%s","verdict":"unknown","reason":"unrecognised_state","state":"%s"}\n' "$WANT_MAC" "$STATE"
    [ "$JSON" = 0 ] && echo "VANTAGE UNKNOWN: unrecognised state '$STATE' for $WANT_MAC — treating as not-ok"
    exit 12 ;;
esac
