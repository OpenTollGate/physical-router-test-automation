# Router Mutex: the ONE machine-global bench lease

Status: reworked 2026-10-17 (PRTA-REVIVE). The old `routers.lock` protocol
described below is **retired** — see "What was wrong" at the end.

## Problem

Multiple agent sessions (or humans, or CI lanes) may need to drive the same
physical router. Without coordination, two sessions can deploy different
binaries, modify configs, or restart services at the same time, producing test
failures that look like product bugs and corrupt the bench for the next run.

## Solution: one flock-backed lease, machine-global

`lib/bench_lock.py` — `BenchLock` — is the only mechanism. It has two parts:

1. **`flock` on a single lock file.** The kernel owns the mutual exclusion. When
   the holder process dies, the flock disappears: a crashed agent can never
   wedge the bench.
2. **A holder line** (human metadata, not authority):
   `<profile> pid=<pid> purpose=<purpose> since=<iso8601> task=<id|-> host=<host>`

**Location** — machine-global, never per-worktree, never `/tmp`:

| resource | lock file |
|---|---|
| bench router (GL-MT3000) | `~/.hermes/state/bench-mt3000.lock` |
| ESP32 board A / B / C | `~/.hermes/state/esp32-board-{a,b,c}.lock` |

Overrides: `TOLLGATE_BENCH_LOCK` / `BENCH_LOCK_PATH` for the bench,
`LOCK_DIR` for the boards. The boards use **the same** mechanism with a
board-scoped filename — there is no second implementation.

## Using it

```sh
make lock PHASE="part B: cold boot testing"   # take the lease (blocks; Ctrl-C releases)
make lock-status                              # who owns it right now
make unlock                                   # release (clears the holder line)
make force-unlock                             # clear a STALE holder line
```

The same CLI, for scripts and CI:

```sh
scripts/hw-bench-lease status                 # state + holder + retired-lock report
scripts/hw-bench-lease require                # exit 0 iff held (Makefile guard)
scripts/hw-bench-lease exec --purpose "smoke" -- <cmd>     # lease for the cmd's lifetime
scripts/hw-bench-lease exec --purpose "smoke" --check-idle -- <cmd>   # + idle gate
```

Exit codes: `2` busy, `3` stale, `4` idle-refused, `5` idle-unprobeable,
`0` ok, otherwise the child's own exit code.

From Python:

```python
from lib.bench_lock import BenchLock, BenchBusy

with BenchLock(purpose="prta install-path e2e", task_id="t_a05094ad") as lock:
    subprocess.run(cmd, env=lock.child_env())   # children pass `bench-lock require`
```

`lib/router_lock.RouterLock` and `lib/hardware_lock` delegate to the same
`BenchLock`; their public APIs are unchanged.

## Busy, stale, and the 93-day file

| state | meaning | what to do |
|---|---|---|
| `held` | a live process holds the flock | wait, or coordinate with the named holder |
| `stale` | a holder line exists but **no flock backs it** — the owner died | `hw-bench-lease release` (or re-run with `--reclaim-stale`) |
| `free` | no flock, no holder line | take it |

Recovery from `stale` is deliberately **explicit**: the line names who died, so
an operator can confirm nothing is live before clearing it.

`hw-bench-lease status` additionally sweeps for **retired** lock files
(`routers.lock`, `hardware.lock`, `tollgate_hardware.lock`) in this checkout and
in sibling worktrees, and reports their age:

```
retired lock files still on disk (NOT authoritative, nothing reads them):
  [STALE] /home/u/worktrees/old/prta/routers.lock (age=93.3d, size=137B)
  remove with: scripts/hw-bench-lease prune-legacy
```

`prune-legacy` removes stale ones (`--all` for all, `--dry-run` to preview).
It never touches the lease itself.

## What was wrong before (the incident this closes)

Three inconsistent mechanisms, none machine-global, none flock-authoritative:

- `Makefile`'s repo-local `hardware.lock` **presence check** — per-worktree, so a
  lock in one checkout was invisible from another, and a stale file blocked
  forever.
- `lib/hardware_lock.py`'s `/tmp/tollgate_hardware.lock` JSON — a 2-hour age
  heuristic, no mutual exclusion, and `/tmp` is not shared reliably.
- `lib/router_lock.py`'s `routers.lock` (and `mint-health/Makefile`'s own copy) —
  a file with a timestamp, no flock; staleness guessed from mtime.

Result: the only live lock file in the fleet was **93 days stale in a sibling
worktree**, while the main worktree had none — so a session that trusted its
own checkout saw "unlocked" and a session that found the stale file saw
"locked". Both were wrong at the same time.

`lib/session_verify.check_balance_api` (the read-only idle-gate primitive) is
now wired into the lease as `exec --check-idle`: a mutating run refuses to
start while a paying session is live, and fails closed when `/balance` is not
probeable. It was previously implemented but connected to nothing.
