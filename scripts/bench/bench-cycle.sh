#!/usr/bin/env bash
# bench-cycle.sh — one OpenWrt bench run for the TollGate trust pipeline.
#
#   acquire the bench lock
#     -> (optional) clean flash of a lane image
#     -> install the package under test
#     -> smoke: module running + portal answering
#     -> radios OFF            <- GUARANTEED by a trap (EXIT/INT/TERM)
#
# The radios-off teardown runs on EVERY exit path, so a failed, killed or
# Ctrl-C'd run still leaves the APs down (neighbours cannot wander in).
#
# Usage:
#   bench-cycle.sh --router IP --lane 24/ipk|25/apk|otg \
#       [--image FILE] [--pkg FILE] [--no-flash] [--confirm WIPE] \
#       [--lock FILE] [--result FILE]
#
# Env:  TOLLGATE_LUCI_PASSWORD  (default: benchpw2026)
#       BENCH_SSH_OPTS          (extra ssh opts)
#
# Exit: 0 ok; 2 usage; 3 lock busy; 10 flash; 20 install; 30 smoke.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PW="${TOLLGATE_LUCI_PASSWORD:-benchpw2026}"
SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
          -o ConnectTimeout=10 -o PreferredAuthentications=password
          -o PubkeyAuthentication=no)
[ -n "${BENCH_SSH_OPTS:-}" ] && SSH_OPTS+=($BENCH_SSH_OPTS)

ROUTER=""; LANE=""; IMAGE=""; PKG=""; NO_FLASH=0; CONFIRM=""
LOCK="$HOME/.hermes/state/bench.lock"; RESULT=""

usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --router) ROUTER="$2"; shift 2 ;;
    --lane)   LANE="$2";   shift 2 ;;
    --image)  IMAGE="$2";  shift 2 ;;
    --pkg)    PKG="$2";    shift 2 ;;
    --lock)   LOCK="$2";   shift 2 ;;
    --result) RESULT="$2"; shift 2 ;;
    --no-flash) NO_FLASH=1; shift ;;
    --confirm) CONFIRM="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done

[ -n "$ROUTER" ] && [ -n "$LANE" ] || usage
case "$LANE" in 24/ipk|25/apk|otg) ;; *) echo "bad --lane: $LANE" >&2; exit 2 ;; esac
# A destructive flash requires an explicit acknowledgement.
if [ "$NO_FLASH" = 0 ] && [ -n "$IMAGE" ] && [ "$CONFIRM" != "WIPE" ]; then
  echo "refusing: a clean flash wipes config; pass --confirm WIPE" >&2; exit 2
fi

mkdir -p "$(dirname "$LOCK")" "$(dirname "${RESULT:-/dev/null}")" 2>/dev/null || true

log() { printf '[bench] %s\n' "$*" >&2; }
ssh_r() { SSHPASS="$PW" sshpass -e ssh "${SSH_OPTS[@]}" "root@$ROUTER" "$1"; }
scp_r() { SSHPASS="$PW" sshpass -e scp -O "${SSH_OPTS[@]}" "$1" "root@$ROUTER:$2"; }

cleanup() {
  local rc=$?
  log "teardown: radios OFF (rc=$rc)"
  # Never fail the teardown; retry once, log if it cannot reach the box.
  "$HERE/radios-off.sh" "$ROUTER" >/dev/null 2>&1 || \
    { log "WARN: could not reach $ROUTER for radios-off (will re-assert)"; sleep 2
      "$HERE/radios-off.sh" "$ROUTER" >/dev/null 2>&1 || \
      log "WARN: radios-off FAILED for $ROUTER"; }
  exit "$rc"
}
trap cleanup EXIT INT TERM

wait_ssh() {
  local i
  for i in $(seq 1 60); do
    ssh_r 'echo up' 2>/dev/null | grep -q up && return 0
    sleep 5
  done
  return 1
}

# ── run under the shared bench lock ─────────────────────────────────────────
exec 9>"$LOCK"
if ! flock -n 9; then echo "[bench] another run holds $LOCK" >&2; exit 3; fi
echo "bench-cycle lane=$LANE router=$ROUTER pid=$$ started=$(date -u +%FT%TZ)" >&9 2>/dev/null || true

json() { printf '{"lane":"%s","router":"%s","flash":"%s","install":"%s","smoke":"%s","rc":%s}\n' \
  "$LANE" "$ROUTER" "$1" "$2" "$3" "$4"; }

FLASH=skipped; INSTALL=skipped; SMOKE=skipped

# ── 1. clean flash ──────────────────────────────────────────────────────────
if [ "$NO_FLASH" = 0 ] && [ -n "$IMAGE" ]; then
  log "flashing $IMAGE (sysupgrade -n)"
  scp_r "$IMAGE" /tmp/fw.bin || { json failed skipped skipped 10; exit 10; }
  ssh_r "sysupgrade -n /tmp/fw.bin" >/dev/null 2>&1 || true
  sleep 20
  wait_ssh || { log "router did not come back after flash"; json done skipped skipped 10; exit 10; }
  FLASH=ok
fi

# ── 2. install package ──────────────────────────────────────────────────────
if [ -n "$PKG" ]; then
  log "installing $PKG"
  scp_r "$PKG" /tmp/pkg.pkg || INSTALL=scp_failed
  case "$LANE" in
    24/ipk) ssh_r "TOLLGATE_ADMIN_PASSWORD='$PW' opkg install --force-reinstall /tmp/pkg.pkg" ;;
    25/apk|otg) ssh_r "apk add --allow-untrusted --force-overwrite /tmp/pkg.pkg" ;;
  esac >/dev/null 2>&1 && INSTALL=ok || INSTALL=failed
fi

# ── 3. smoke ────────────────────────────────────────────────────────────────
if ssh_r 'pgrep -f tollgate-wrt >/dev/null' 2>/dev/null; then
  if ssh_r 'wget -qO- http://127.0.0.1/ 2>/dev/null | grep -qi tollgate' 2>/dev/null; then
    SMOKE=portal_ok
  else SMOKE=no_portal; fi
else SMOKE=no_module; fi

log "smoke: flash=$FLASH install=$INSTALL smoke=$SMOKE"
[ -n "$RESULT" ] && json "$FLASH" "$INSTALL" "$SMOKE" 0 >"$RESULT"

# cleanup trap will radios-off and exit with this status
[ "$SMOKE" = portal_ok ] && exit 0
[ "$SMOKE" = no_module ] && exit 30
exit 30
