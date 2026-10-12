#!/usr/bin/env bash
# bench-wifi-off-watchdog.sh — assert radios OFF whenever the bench is idle.
#
# The bench cycle drops the APs in its own teardown, but a run that dies without
# running the trap (SIGKILL, host reboot, power loss) would leave SSIDs up. This
# watchdog is the backstop: run it on a timer; if no run currently holds the
# bench lock, force radios off on every configured router.
#
# Env:
#   BENCH_LOCK      bench lock file (default ~/.hermes/state/bench.lock)
#   BENCH_ROUTERS   space-separated router IPs to keep quiet (default: none -> no-op)
#   TOLLGATE_LUCI_PASSWORD  router password (default benchpw2026)
#
# Safe to run repeatedly; it never touches a router while the lock is held.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LOCK="${BENCH_LOCK:-$HOME/.hermes/state/bench.lock}"
ROUTERS="${BENCH_ROUTERS:-}"

[ -n "$ROUTERS" ] || { echo "bench-wifi-off-watchdog: BENCH_ROUTERS unset — nothing to do"; exit 0; }
mkdir -p "$(dirname "$LOCK")" 2>/dev/null || true

# If the lock is held, a run is active — leave it alone.
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "bench-wifi-off-watchdog: bench busy — skipping"
  exit 0
fi

rc=0
for r in $ROUTERS; do
  if "$HERE/radios-off.sh" "$r" >/dev/null 2>&1; then
    echo "bench-wifi-off-watchdog: $r radios off"
  else
    echo "bench-wifi-off-watchdog: $r unreachable or still radiating" >&2
    rc=1
  fi
done
exit "$rc"
