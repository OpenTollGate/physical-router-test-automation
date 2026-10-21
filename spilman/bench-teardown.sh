#!/usr/bin/env bash
# spilman-bench-teardown.sh — reverse of bench-setup.sh.
#
# Stops the mint and the module HTTP server this lane started. Keeps mint data
# unless --purge is passed, so a failed run can be inspected before it is lost.
#
# USAGE
#   bash spilman/bench-teardown.sh [--purge]

set -uo pipefail

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

RUNDIR="${SPILMAN_RUNDIR:-$HOME/.local/state/spilman-bench}"

log() { printf '[teardown] %s\n' "$*"; }

stop_pidfile() {
  local name="$1" pidfile="$RUNDIR/$1.pid"
  if [ -f "$pidfile" ]; then
    local pid
    pid="$(cat "$pidfile")"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      for _ in $(seq 1 10); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
      log "stopped $name (pid $pid)"
    else
      log "$name pid $pid was not running"
    fi
    rm -f "$pidfile"
  else
    log "$name has no pidfile (never started by this lane?)"
  fi
}

stop_pidfile mint
stop_pidfile http

if [ "$PURGE" -eq 1 ]; then
  log "purging $RUNDIR including mint data"
  rm -rf "$RUNDIR"
else
  log "kept $RUNDIR (mint data preserved); pass --purge to delete"
fi
