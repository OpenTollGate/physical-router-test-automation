#!/usr/bin/env bash
# spilman-bench-setup.sh — provision the Spilman channel bench lane.
#
# WHAT THIS PROVISIONS
#
# A Spilman channel needs a Cashu mint BOTH parties can reach. The router cannot
# host one usefully (~3 MB flash headroom, no curl/sqlite3), so the mint lives on
# this host, bound to the LAN.
#
# THE LOAD-BEARING DETAIL: cdk-spilman-test-mint defaults to 127.0.0.1
# (crates/cdk-spilman-test-mint/src/lib.rs:72) and both upstream launch scripts
# hardcode loopback. Started that way the HOST sees a healthy mint and the ROUTER
# sees nothing — a silent failure that looks like a router fault. This script
# always binds 0.0.0.0 with a matching --base-url, and then PROVES the bind by
# connecting to the LAN address rather than to loopback.
#
# WHAT IT IS NOT
#
# It does not touch the router. It does not flash, restart, or write to the
# device. Router work is spilman_router_lane.py and it takes the serial mutex.
#
# USAGE
#   bash spilman/bench-setup.sh [--lan-ip <ip>] [--port 3338] [--http-port 18999]
#                               [--module-binary <path>] [--skip-module-server]
#
# ENV OVERRIDES
#   SPILMAN_MINT_PORT (3338), SPILMAN_HTTP_PORT (18999),
#   SPILMAN_REPO (default ~/repos/cashu_spilman_channels)

set -euo pipefail

LAN_IP=""
PORT="${SPILMAN_MINT_PORT:-3338}"
HTTP_PORT="${SPILMAN_HTTP_PORT:-18999}"
MODULE_BINARY=""
SKIP_MODULE_SERVER=0
SPILMAN_REPO="${SPILMAN_REPO:-$HOME/repos/cashu_spilman_channels}"
RUNDIR="${SPILMAN_RUNDIR:-$HOME/.local/state/spilman-bench}"

while [ $# -gt 0 ]; do
  case "$1" in
    --lan-ip)              LAN_IP="$2"; shift 2 ;;
    --port)                PORT="$2"; shift 2 ;;
    --http-port)           HTTP_PORT="$2"; shift 2 ;;
    --module-binary)       MODULE_BINARY="$2"; shift 2 ;;
    --skip-module-server)  SKIP_MODULE_SERVER=1; shift ;;
    -h|--help)             sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

log()  { printf '[setup] %s\n' "$*"; }
fail() { printf '[setup] FAIL: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Discover the LAN address. A loopback bind is not sufficient: the router
#    must reach this host over the LAN.
# ---------------------------------------------------------------------------
if [ -z "$LAN_IP" ]; then
  LAN_IP="$(ip -4 route get 1.1.1.1 2>/dev/null \
            | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1); exit}')"
fi
[ -n "$LAN_IP" ] || fail "could not discover a LAN address; pass --lan-ip"
log "LAN address: $LAN_IP"

if [ "$LAN_IP" = "127.0.0.1" ] || [ "$LAN_IP" = "::1" ]; then
  fail "discovered address is loopback ($LAN_IP) — the router could never reach it"
fi

mkdir -p "$RUNDIR"

# ---------------------------------------------------------------------------
# 2. Build the test mint if it is not already built.
# ---------------------------------------------------------------------------
MINTD="$SPILMAN_REPO/target/release/cdk-spilman-test-mintd"
if [ ! -x "$MINTD" ]; then
  log "building cdk-spilman-test-mintd (this takes several minutes on a cold cache)"
  [ -d "$SPILMAN_REPO" ] || fail "spilman repo not found at $SPILMAN_REPO"
  ( cd "$SPILMAN_REPO" && cargo build -p cdk-spilman-test-mint --release )
fi
[ -x "$MINTD" ] || fail "test mint binary missing after build: $MINTD"
log "test mint binary: $MINTD ($(stat -c %s "$MINTD") bytes)"

# ---------------------------------------------------------------------------
# 3. Start the mint bound to the LAN.
# ---------------------------------------------------------------------------
if curl -sf --max-time 2 "http://$LAN_IP:$PORT/v1/info" >/dev/null 2>&1; then
  log "a mint already answers on $LAN_IP:$PORT — reusing it"
else
  log "starting test mint on 0.0.0.0:$PORT (base-url http://$LAN_IP:$PORT)"
  # 9>&- 8>&- : never let the daemon inherit a bench lock fd.
  nohup "$MINTD" \
      --listen-host 0.0.0.0 \
      --port "$PORT" \
      --base-url "http://$LAN_IP:$PORT" \
      > "$RUNDIR/mint.log" 2>&1 9>&- 8>&- &
  echo $! > "$RUNDIR/mint.pid"
  log "mint pid $(cat "$RUNDIR/mint.pid"); log $RUNDIR/mint.log"

  for _ in $(seq 1 60); do
    if curl -sf --max-time 2 "http://$LAN_IP:$PORT/v1/info" >/dev/null 2>&1; then break; fi
    sleep 1
  done
fi

# Prove the bind through the LAN address, NOT loopback. This is the whole point.
curl -sf --max-time 5 "http://$LAN_IP:$PORT/v1/info" >/dev/null \
  || fail "mint is not answering on the LAN address $LAN_IP:$PORT (a loopback-only bind would look healthy locally and be unreachable from the router)"
log "mint reachable on the LAN address"

# ---------------------------------------------------------------------------
# 4. Optional: serve a module binary for the router-side wget.
#    Serial is NOT the transfer path — BusyBox wget over HTTP is.
# ---------------------------------------------------------------------------
if [ "$SKIP_MODULE_SERVER" -eq 0 ]; then
  SRVDIR="$RUNDIR/serve"
  mkdir -p "$SRVDIR"
  if [ -n "$MODULE_BINARY" ]; then
    [ -f "$MODULE_BINARY" ] || fail "module binary not found: $MODULE_BINARY"
    cp "$MODULE_BINARY" "$SRVDIR/"
    log "staged $(basename "$MODULE_BINARY") for wget"
  else
    log "no --module-binary given; serving an empty directory (wget step will be skipped)"
  fi

  if curl -sf --max-time 2 "http://$LAN_IP:$HTTP_PORT/" >/dev/null 2>&1; then
    log "module server already answering on $LAN_IP:$HTTP_PORT"
  else
    log "starting module HTTP server on 0.0.0.0:$HTTP_PORT"
    ( cd "$SRVDIR" && nohup python3 -m http.server "$HTTP_PORT" --bind 0.0.0.0 \
        > "$RUNDIR/http.log" 2>&1 9>&- 8>&- & echo $! > "$RUNDIR/http.pid" )
    sleep 1
  fi
fi

# ---------------------------------------------------------------------------
# 5. Emit the environment the lane and the module consume.
# ---------------------------------------------------------------------------
cat > "$RUNDIR/env.sh" <<EOF
SPILMAN_MINT_URL=http://$LAN_IP:$PORT
SPILMAN_MODULE_URL=http://$LAN_IP:$HTTP_PORT
SPILMAN_LAN_IP=$LAN_IP
EOF
log "wrote $RUNDIR/env.sh"
cat "$RUNDIR/env.sh"

echo
log "READY. Point the router's accepted_mints at SPILMAN_MINT_URL above."
log "Teardown: bash spilman/bench-teardown.sh"
