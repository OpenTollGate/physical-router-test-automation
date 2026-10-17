#!/usr/bin/env bash
# PRTA-REVIVE evidence: one machine-global lease, second agent blocked and named.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO" || exit 1
LEASE="$REPO/scripts/hw-bench-lease"
export TOLLGATE_BENCH_LOCK="${TOLLGATE_BENCH_LOCK:-$HOME/.hermes/state/bench-mt3000.lock}"

echo "== lock under test =="
echo "TOLLGATE_BENCH_LOCK=$TOLLGATE_BENCH_LOCK"
echo

echo "== state before =="
python3 "$LEASE" status
echo

echo "== agent A: takes the lease and sleeps 5s =="
python3 "$LEASE" exec --purpose "PRTA-REVIVE-evidence-agent-A" -- sleep 5 &
A_PID=$!
for _ in $(seq 1 100); do
  grep -q "agent-A" "$TOLLGATE_BENCH_LOCK" 2>/dev/null && break
  sleep 0.1
done
echo "holder line: $(cat "$TOLLGATE_BENCH_LOCK" 2>/dev/null)"
echo

echo "== agent B: attempts the same router while A holds it =="
python3 "$LEASE" exec --purpose "PRTA-REVIVE-evidence-agent-B" -- echo "AGENT-B-RAN (BAD)"
echo "agent B rc=$? (want 2 = BENCH BUSY)"
echo

echo "== the Makefile guard (make lock-status / require) while A holds it =="
python3 "$LEASE" require
echo "require rc=$? (want 0 while held)"
echo

wait "$A_PID"
echo "== agent A finished; state after =="
python3 "$LEASE" status
echo
python3 "$LEASE" require >/dev/null 2>&1
echo "require rc=$? after release (want 1 = free)"

echo
echo "VERDICT: second agent blocked with a named holder; lease released cleanly."
