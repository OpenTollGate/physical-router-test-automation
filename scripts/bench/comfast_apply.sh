#!/usr/bin/env bash
# comfast_apply.sh — reproduce the COMFAST CF-WR632AX OpenWrt test-target config.
#
# This is idempotent and runs over the 2.5G WAN management path. It expects:
#   * OpenWrt 25.12.5 already installed (see docs/FLEET-TEST-ROUTERS.md);
#   * DHCP reservation 192.168.50.219 on the MT6000;
#   * the lease-scoped bench key or TOLLGATE_SSH_KEY.
#
# Run it inside the bench lease:
#   scripts/hw-bench-lease exec --purpose "comfast apply" -- \
#     scripts/prta-bench-run.sh -- scripts/bench/comfast_apply.sh
#
# The APs are left disabled; tests arm them with quiesce-router.py --enable.
# The root/LuCI password remains OpenBao-managed and is not written here.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ROUTER_ID="${COMF_ROUTER_ID:-comfast-b3ba}"
HOST="${COMF_HOST:-192.168.50.219}"
KEY="${TOLLGATE_SSH_KEY:-}"
KNOWN="${TOLLGATE_KNOWN_HOSTS:-/tmp/comfast-known-hosts}"
JUMP="${TOLLGATE_SSH_JUMP:-}"
ALLOW_UNLOCKED=0
DRY_RUN=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --allow-unlocked) ALLOW_UNLOCKED=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

say() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Fail closed for production/control devices.
python3 "$ROOT/scripts/check-protected.py" --address "$HOST" >/dev/null || die "$HOST is protected"

if [ "$DRY_RUN" != 1 ] && [ "$ALLOW_UNLOCKED" != 1 ]; then
  (cd "$ROOT" && python3 -c 'from lib.hardware_lock import require_hardware_lock; require_hardware_lock()')
fi

if [ -z "$KEY" ] && [ -f "$ROOT/config/routers.json" ]; then
  KEY="$(python3 - "$ROOT/config/routers.json" "$ROUTER_ID" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
r = (doc.get("routers") or {}).get(sys.argv[2]) or {}
print(r.get("sshKey") or "")
PY
)"
fi
if [ -n "$KEY" ] && [ ! -f "$KEY" ]; then
  die "SSH key $KEY not found; run inside scripts/prta-bench-run.sh (it materializes the OpenBao bench key), or pass TOLLGATE_SSH_KEY"
fi
[ -n "$KEY" ] || die "TOLLGATE_SSH_KEY is required"

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new
          -o "UserKnownHostsFile=$KNOWN")
JUMP_ARGS=()
[ -n "$JUMP" ] && JUMP_ARGS=(-J "$JUMP")

REMOTE="$(cat <<'EOS'
set -e
if ! uci -q get wireless.radio0 >/dev/null 2>&1; then
  wifi config >/dev/null 2>&1 || true
fi
uci set system.@system[0].hostname='comfast-b3ba'
uci set network.lan.device='br-lan'
uci set network.lan.proto='static'
uci set network.lan.ipaddr='192.168.1.1'
uci set network.lan.netmask='255.255.255.0'
uci set network.wan.device='eth0'
uci set network.wan.proto='dhcp'
uci set wireless.radio0.channel='11'
uci set wireless.radio0.country='DE'
uci set wireless.radio0.htmode='HE20'
uci set wireless.radio0.disabled='0'
uci set wireless.radio1.channel='36'
uci set wireless.radio1.country='DE'
uci set wireless.radio1.htmode='HE80'
uci set wireless.radio1.disabled='0'
uci set wireless.default_radio0.device='radio0'
uci set wireless.default_radio0.network='lan'
uci set wireless.default_radio0.mode='ap'
uci set wireless.default_radio0.ssid='TollGate-COMFAST'
uci set wireless.default_radio0.encryption='none'
uci set wireless.default_radio0.disabled='1'
uci set wireless.default_radio1.device='radio1'
uci set wireless.default_radio1.network='lan'
uci set wireless.default_radio1.mode='ap'
uci set wireless.default_radio1.ssid='TollGate-COMFAST-5G'
uci set wireless.default_radio1.encryption='none'
uci set wireless.default_radio1.disabled='1'
uci set firewall.allow_x280_mgmt=rule
uci set firewall.allow_x280_mgmt.name='allow-x280-mgmt'
uci set firewall.allow_x280_mgmt.src='wan'
uci set firewall.allow_x280_mgmt.src_ip='192.168.50.10/32 192.168.50.180/32'
uci set firewall.allow_x280_mgmt.proto='tcp'
uci set firewall.allow_x280_mgmt.dest_port='22 80 443'
uci set firewall.allow_x280_mgmt.target='ACCEPT'
uci set firewall.allow_x280_mgmt.family='ipv4'
# Do not disable password auth until at least one key is installed.
if [ "$(grep -c '^ssh-' /etc/dropbear/authorized_keys 2>/dev/null || echo 0)" -ge 1 ]; then
  uci set dropbear.main.PasswordAuth='off'
  uci set dropbear.main.RootPasswordAuth='off'
fi
uci commit system
uci commit network
uci commit wireless
uci commit firewall
uci commit dropbear 2>/dev/null || true
wifi reload >/dev/null 2>&1 || wifi up >/dev/null 2>&1 || true
/etc/init.d/firewall reload >/dev/null 2>&1 || true
# Do not restart dropbear here: it would terminate this SSH session before the
# verification below. The committed config applies on the next dropbear
# restart/reboot; the live target is already key-only.
sleep 3
echo "-- hostname --"; uci get system.@system[0].hostname
echo "-- wans --"; uci show network.wan | grep -E 'device|proto'
echo "-- lans --"; uci show network.lan | grep -E 'device|proto|ipaddr|netmask'
echo "-- aps --"; uci show wireless | grep -E 'default_radio[01]\.(ssid|disabled|channel|htmode|country)'
echo "-- firewall --"; uci show firewall.allow_x280_mgmt
echo "-- dropbear --"; uci show dropbear | grep -E 'PasswordAuth|RootPasswordAuth'
echo "-- ap count --"; iw dev 2>/dev/null | grep -c 'type AP' || true
echo "COMF_APPLY_DONE"
EOS
)"

if [ "$DRY_RUN" = 1 ]; then
  say "dry-run $ROUTER_ID ($HOST)"
  printf '%s\n' "$REMOTE"
  exit 0
fi

say "applying COMFAST OpenWrt config to $ROUTER_ID ($HOST)"
printf '%s\n' "$REMOTE" | ssh "${SSH_OPTS[@]}" "${JUMP_ARGS[@]}" -i "$KEY" "root@$HOST" 'sh -s'
