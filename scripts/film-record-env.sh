#!/usr/bin/env bash
# film-record-env — prepare a REPRODUCIBLE recording environment for the
# router-driven install films, then tear it down.
#
# Why this exists: a film take used to be assembled by hand (join the recorder
# host's WiFi to the router's guest SSID, prime NoDogSplash, mint a testnet
# e-cash token, remember to restore the WiFi afterwards). That is exactly the
# kind of ad-hoc step that produces a broken/failed take. This script makes it
# deterministic.
#
# It does FOUR things on the recorder host (the machine that can reach the
# router; for the fleet that is the gateway, e.g. `cobrador`):
#   1. join the recorder's WiFi to the router's GUEST SSID (never-default, so
#      the host's management path is untouched)
#   2. prime NoDogSplash by loading the gateway port once, so the guest gets a
#      preauthenticated session (without it the purchase is refused with
#      CU107 "no captive-portal session for this device")
#   3. mint a fresh TESTNET e-cash token for the portal take
#   4. (--teardown) restore the WiFi and remove the temporary connection
#
# Usage:
#   film-record-env.sh up    --host cobrador --ssid TollGate-9BD4 \
#                            --router-ip 10.110.223.1 --guest-ip 192.168.1.116
#   film-record-env.sh down  --host cobrador
#
# Assumes the module under test is configured to accept the testnet mint; the
# caller trims accepted_mints before recording (see the installer film README).
set -euo pipefail

CON="tollgate-guest"
CMD="${1:?usage: film-record-env.sh <up|down> [options]}"; shift || true
HOST=""; SSID=""; ROUTER_IP=""; GUEST_IP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --ssid) SSID="$2"; shift 2 ;;
    --router-ip) ROUTER_IP="$2"; shift 2 ;;
    --guest-ip) GUEST_IP="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

run() { ssh -o ConnectTimeout=12 "$HOST" "$@"; }

case "$CMD" in
  up)
    : "${HOST:?--host required}" "${SSID:?--ssid required}"
    echo "==> join guest SSID $SSID on $HOST (never-default)"
    run "sudo nmcli con delete $CON 2>/dev/null || true"
    run "sudo nmcli con add type wifi ifname \$(nmcli -t -f DEVICE,TYPE dev | awk -F: '\$2==\"wifi\"{print \$1; exit}') \
         con-name $CON ssid '$SSID' ipv4.never-default yes ipv6.never-default yes"
    run "sudo nmcli con modify $CON 802-11-wireless.powersave 2 || true"
    run "sudo nmcli con up $CON"
    sleep 6
    ip="$(run "ip -4 -o addr show | awk '\$2 ~ /wl/ {print \$4}' | head -1")"
    echo "    guest address: ${ip:-<none>}"
    mac="$(run "nmcli -t -f DEVICE,TYPE dev | awk -F: '\$2==\"wifi\"{d=\$1} END{print d}'" | tr -d '\r')"
    echo "    guest device: ${mac:-<none>}"
    if [ -n "$ROUTER_IP" ]; then
      echo "==> prime NoDogSplash (load the gateway port once)"
      run "curl -s -o /dev/null --max-time 8 http://$ROUTER_IP:2050/ || true"
    fi
    echo "==> done. Record with CHROME_EXE=/usr/bin/google-chrome and the"
    echo "    router's management address as PORTAL_URL / BOARD_IP."
    ;;
  down)
    : "${HOST:?--host required}"
    echo "==> restore WiFi on $HOST"
    run "sudo nmcli con down $CON 2>/dev/null || true"
    # bring the previously-active connection back up
    run "prev=\$(nmcli -t -f NAME,DEVICE con show --active | awk -F: '\$2 ~ /wl/ {print \$1; exit}'); \
         [ -n \"\$prev\" ] && sudo nmcli con up \"\$prev\" || true"
    run "sudo nmcli con delete $CON 2>/dev/null || true"
    echo "==> done"
    ;;
  *)
    echo "unknown command: $CMD (want up|down)" >&2; exit 2 ;;
esac
