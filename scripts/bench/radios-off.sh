#!/usr/bin/env bash
# radios-off.sh — bring every WiFi AP down on a bench router, over SSH.
#
# Idempotent and safe to run on an idle router. Exit 0 iff no WiFi interface
# remains up. Used by the bench cycle's teardown and by the idle watchdog so a
# router left idle (including after an aborted run) never keeps its SSIDs on.
#
# Usage: radios-off.sh <router-ip> [user]        (default user: root)
# Env:   TOLLGATE_LUCI_PASSWORD (default benchpw2026)
set -euo pipefail

HOST="${1:?usage: radios-off.sh <router-ip> [user]}"
USER_="${2:-root}"
PW="${TOLLGATE_LUCI_PASSWORD:-benchpw2026}"

REMOTE='
set -e
# Disable every wifi-device (radio) and every wifi-iface (AP/STA).
for r in $(uci show wireless 2>/dev/null | sed -n "s/^\(wireless\.[^.]*\)\.=wifi-device$/\1/p"); do
  uci set "$r.disabled=1"
done
for i in $(uci show wireless 2>/dev/null | sed -n "s/^\(wireless\.[^.]*\)\.=wifi-iface$/\1/p"); do
  uci set "$i.disabled=1"
done
uci commit wireless
wifi down 2>/dev/null || true
sleep 2
n="$(iw dev 2>/dev/null | grep -c Interface || true)"
echo "radios_up=$n"
[ "$n" = "0" ] || { echo "ERROR: $n wifi interface(s) still up" >&2; exit 1; }
'

SSHPASS="$PW" sshpass -e ssh \
  -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  -o ConnectTimeout=10 -o PreferredAuthentications=password -o PubkeyAuthentication=no \
  "$USER_@$HOST" "$REMOTE"
