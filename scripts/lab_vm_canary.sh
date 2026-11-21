#!/bin/sh
# lab_vm_canary.sh — regression canary for the lab-VM stability contract (prta#21).
#
# THE DEFECT CLASS THIS PINS
#
# Two failure modes hit the api suite 2x (prta#21), one causal chain:
#
#   1. NETWORK-RESTART WEDGE: a network restart on the DUT (the deploy
#      postinst runs one; #962 proved it can run two) leaves netifd
#      silently wedged when the venue carries leftover wireless state: the
#      uci radio path references a hwsim sysfs name that is MONOTONIC per
#      boot (hwsim2 exists only until the module is reloaded), and/or the
#      netifd-respawned jailed hostapd/wpa_supplicant global loses its ubus
#      registration (reproduced live: one double-restart lost the hostapd
#      global and left every wifi-iface `up:false, pending:false` — netifd
#      gives up with no retry and logs nothing further).
#   2. OVERLAY LOSS ON RESET: the wedged VM times out, the operator or the
#      runner "recovers" it with a hard reset, and the dirty qcow2 loses
#      data — the #246 class (150+ zero-byte binaries that masked breakage
#      for months).
#
# The canary cuts the chain at both links: it FAILS LOUDLY on the wedge
# signature BEFORE anyone reaches for a reset, and it detects the zero-byte
# rot that a past hard reset already caused.
#
# Usage (from the lab host): lab_vm_canary.sh [ssh-host] [ssh-password]
# Exit 0 = healthy; 2 = wedge signature; 3 = overlay rot; 4 = DUT unreachable.

set -u
HOST="${1:-${TOLLGATE_SSH_HOST:-10.99.99.1}}"
PASSWORD="${2:-${TOLLGATE_VIRTUAL_LAB_PASSWORD:-tollgate}}"
OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 -o LogLevel=ERROR"

rem() { sshpass -p "$PASSWORD" ssh $OPTS "root@$HOST" "$1" 2>/dev/null; }

if ! rem true; then
  echo "CANARY FAIL [unreachable]: DUT $HOST does not answer SSH."
  echo "  DO NOT hard-reset (kill -9 QEMU): that is how overlay rot (#246) happens."
  echo "  Recover with: virtual-lab.py stop-poc && virtual-lab.py start-poc  (monitor powerdown path)"
  exit 4
fi

# --- 1. netifd canary: lan must be up -----------------------------------------
lan_state=$(rem "ubus call network.interface.lan status 2>/dev/null | grep -o '\"up\": *[a-z]*' | head -1")
if [ "$lan_state" != "\"up\": true" ]; then
  echo "CANARY FAIL [wedge]: network.interface.lan is not up (state: ${lan_state:-none})"
  echo "  This is the prta#21 network-restart wedge signature."
  echo "  DO NOT hard-reset. Remediate: virtual-lab.py net-restart (ordered restart),"
  echo "  and check for leftover wireless state: /etc/config/wireless on a stock lab"
  echo "  VM is venue pollution (stale hwsim radio paths wedge netifd on every restart)."
  exit 2
fi

# --- 2. wireless wedge canary: if configured, every iface must be up ----------
if rem "test -f /etc/config/wireless"; then
  down_ifaces=$(rem "ubus call network.wireless status 2>/dev/null | grep -c '\"up\": false'")
  if [ "${down_ifaces:-0}" -gt 0 ]; then
    echo "CANARY FAIL [wedge]: wireless config present but ${down_ifaces} iface(s) up:false"
    echo "  netifd has silently given up (the no-retry wedge, prta#21)."
    echo "  Remediate: virtual-lab.py net-restart; if the uci radio 'path' references"
    echo "  an hwsim sysfs name, it is stale (names are monotonic per boot) — remove"
    echo "  /etc/config/wireless unless this lane explicitly drives wireless."
    exit 2
  fi
fi

# --- 3. overlay-rot canary: zero-byte files in system trees (#246) ------------
zeros=$(rem "find /usr/bin /usr/sbin /usr/lib /lib -type f -size 0 2>/dev/null | grep -v '/opkg/info/' | head -5 | tr '\n' ' '")
if [ -n "$zeros" ]; then
  echo "CANARY FAIL [rot]: zero-byte system files (the #246 dirty-overlay class):"
  echo "  $zeros"
  echo "  A past hard reset damaged this overlay. Remediate:"
  echo "  virtual-lab.py stop-poc && virtual-lab.py start-poc --fresh"
  exit 3
fi

echo "CANARY OK: lan up; wireless consistent; no zero-byte rot on ${HOST}"
exit 0
