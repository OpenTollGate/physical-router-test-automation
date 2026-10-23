#!/usr/bin/env bash
# PRTA-REVIVE evidence: a 93-day-stale retired lock file cannot block, is
# reported, and is reclaimed; the authoritative lease is unaffected.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO" || exit 1
LEASE="$REPO/scripts/hw-bench-lease"

TMP="$(mktemp -d "${TMPDIR:-/var/tmp}/prta-stale-XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
export TOLLGATE_BENCH_LOCK="$TMP/bench-mt3000.lock"

echo "== scenario: a sibling worktree left routers.lock behind 93 days ago =="
mkdir -p "$TMP/sibling-worktree/mint-health"
printf 'locked: true\nbranch: dead-branch\nsession: dead-agent@bench\ntimestamp: 2026-07-16T00:00:00Z\nphase: crashed run\n' \
  > "$TMP/sibling-worktree/mint-health/routers.lock"
touch -d '93 days ago' "$TMP/sibling-worktree/mint-health/routers.lock"
printf 'locked: true\nsession: dead-agent@bench\ntimestamp: 2026-07-16T00:00:00Z\n' \
  > "$TMP/sibling-worktree/hardware.lock"
touch -d '93 days ago' "$TMP/sibling-worktree/hardware.lock"
ls -l --time-style=long-iso "$TMP/sibling-worktree/mint-health/routers.lock" \
  "$TMP/sibling-worktree/hardware.lock"
echo

echo "== 1) the lease is FREE — the stale file is not authority =="
python3 "$LEASE" status --legacy-root "$TMP/sibling-worktree"
echo "status rc=$? (want 0)"
echo

echo "== 2) a run is NOT blocked by the stale file =="
python3 "$LEASE" exec --purpose "stale-proof" --legacy-root "$TMP/sibling-worktree" -- echo "RAN DESPITE 93-DAY routers.lock"
echo "exec rc=$? (want 0)"
echo

echo "== 3) prune-legacy reclaims it (dry-run first) =="
python3 "$LEASE" prune-legacy --legacy-root "$TMP/sibling-worktree" --dry-run
python3 "$LEASE" prune-legacy --legacy-root "$TMP/sibling-worktree"
echo "prune rc=$?"
echo

echo "== 4) nothing left, lease untouched =="
python3 "$LEASE" status --legacy-root "$TMP/sibling-worktree"
echo

echo "== 5) a STALE lease holder line (owner died mid-run) refuses, then reclaims =="
printf 'ghost-worker pid=4128000 purpose=crashed-run since=2026-07-16T00:00:00+00:00 task=t_dead host=bench\n' \
  > "$TOLLGATE_BENCH_LOCK"
python3 "$LEASE" status | sed -n '1,4p'
python3 "$LEASE" exec --purpose "retry" -- true; echo "exec rc=$? (want 3 = STALE, explicit recovery)"
python3 "$LEASE" exec --purpose "retry" --reclaim-stale -- true; echo "exec --reclaim-stale rc=$? (want 0)"
echo

echo "VERDICT: stale retired locks are reported, non-blocking and prunable;"
echo "         stale lease holder lines refuse by default and reclaim explicitly."
