#!/usr/bin/env bash
# Board-lease concurrency smoke: agent A holds board-a, agent B must be refused.
# Same mechanism as the bench lease (lib.bench_lock.BenchLock), board-scoped file.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
cd "$REPO" || exit 1

export ESP32_LOCK_DIR="${ESP32_LOCK_DIR:-$HOME/.hermes/state}"
LOCK="$ESP32_LOCK_DIR/esp32-board-a.lock"
LEASE="$REPO/scripts/hw-bench-lease"

echo "== before =="
python3 "$LEASE" status --lock "$LOCK" --no-legacy-scan

echo
echo "== agent A: hold board-a for 4s =="
python3 "$LEASE" exec --lock "$LOCK" --resource "Board A" --purpose "board-lease-A" -- sleep 4 &
A_PID=$!
for _ in $(seq 1 50); do
  [ -s "$LOCK" ] && break
  sleep 0.1
done
echo "holder: $(cat "$LOCK" 2>/dev/null)"

echo
echo "== agent B: tries the same board while A holds it =="
python3 "$LEASE" exec --lock "$LOCK" --resource "Board A" --purpose "board-lease-B" -- echo "SHOULD-NOT-RUN"
echo "agent B rc=$? (want 2 = BUSY)"

echo
echo "== the bench lease is unaffected by the board lease =="
python3 "$LEASE" status --no-legacy-scan | sed -n '1,2p'

wait "$A_PID"
echo
echo "== after A exits =="
python3 "$LEASE" status --lock "$LOCK" --no-legacy-scan
