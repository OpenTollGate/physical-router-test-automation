#!/usr/bin/env bash
# run-with-quiesce.sh — run any router-touching command, then disable APs.
#
# The command runs inside the caller's bench lease. If no lease is held, the
# wrapper refuses unless --allow-unlocked is passed (read-only/offline only).
#
# Usage:
#   scripts/run-with-quiesce.sh --router alpha -- ./scripts/run-tests.sh ...
#   TOLLGATE_ROUTER_ID=alpha scripts/run-with-quiesce.sh -- ./scripts/run-api.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROUTER_ID="${TOLLGATE_ROUTER_ID:-}"
ALLOW_UNLOCKED=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --router) ROUTER_ID="$2"; shift 2 ;;
    --allow-unlocked) ALLOW_UNLOCKED=1; shift ;;
    --) shift; break ;;
    *) break ;;
  esac
done
[ "$#" -gt 0 ] || { echo "usage: $0 [--router ID] [--allow-unlocked] -- <command...>" >&2; exit 2; }

if [ "$ALLOW_UNLOCKED" != 1 ] && [ "${TOLLGATE_ALLOW_UNLOCKED:-0}" != 1 ]; then
  if ! (cd "$REPO_DIR" && python3 -c 'from lib.hardware_lock import require_hardware_lock; require_hardware_lock()' >/dev/null 2>&1); then
    echo "ERROR: bench lease not held; use scripts/hw-bench-lease exec" >&2
    exit 1
  fi
fi

cleanup() {
  if [ -n "$ROUTER_ID" ]; then
    python3 "$SCRIPT_DIR/quiesce-router.py" --router "$ROUTER_ID" --allow-unlocked >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

"$@"
