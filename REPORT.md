# Runner lifecycle safety — bounded fail-closed milestone

Base: `d5e5fc30a8f0bd27ea3b5f26adcd3b38438eb7b2` (branch `pr/fleet-runner-lifecycle`,
worktree `~/worktrees/prta-runner-lifecycle`). Prior milestone reports preserved below.

## Outcome

The production runner now **refuses fleet execution** (`exit 75`,
reason `fleet_lifecycle_unavailable`) before it opens the authority store, creates a
result tree, takes a reservation, or spawns a child. This is the permitted bounded
fallback: the coordinator-owned supervised lifecycle could not be wired safely in this
milestone, so the unsafe partial happy path is removed instead of shipped.

Guarded by `scripts/run-profile.py::require_supported_fleet_lifecycle`, called from
`main()` after profile parsing and **before** `run_id`/results-layout construction.
Dry runs and non-fleet profiles (`--fleet-manifest` absent) are unchanged.

Preserved, deliberately:
- Manifest authorization (`admission_from_args`, `resolve_target`, permissions, domain
  union) is untouched and still covered — it is the contract any future lifecycle must
  satisfy. It is simply unreachable from `main()` for a non-dry-run fleet invocation.
- Read-only dry run: no store minted, no reservation, no child.
- Non-fleet execution: `tee_run` still tees the child's output to
  `raw/<runner>/output.log`, returns the child's exit status, and `--no-render`/collect/
  render flow is unchanged.

## Why fail-closed instead of wiring `FleetScheduler`

Inspected before deciding (no code copied, no second safety mechanism invented):

1. **No profile→place binding.** `ScheduleRequest` takes `resource_id`, `command`,
   `cwd`, `env`; nothing in `run-profile.py` derives `coordinator`/`place` from the
   selected manifest resource, and nothing checks that the profile's
   `TOLLGATE_SSH_HOST`/endpoint targets that place. Acquiring place X while pytest
   drives whatever the env points at is the two-DUT bug, not a fix for it.
2. **Fixture double acquisition.** `tests/conftest.py` (router fixture) independently
   calls `plan_venue`/`venue_mutex`/`LabgridBench` for venue profiles. A runner-side
   `Lease` would add a *second* owner; there is no ownership-handoff seam
   (`TOLLGATE_USE_HARDWARE_LOCK` only suppresses the file lock, not the place).
3. **Supervision gaps in the library itself.** `Lease._terminate` sends SIGTERM/SIGKILL
   to the group but never re-verifies the group is gone; `Lease.run` suppresses
   `AuthorityError` from `renew()` (a failed renewal continues until the TTL check
   notices); `_compensate` treats "no place and no pending cleanup" as clean without
   asking the coordinator. `Lease.run` also does not tee output, so adopting it
   as-is would silently drop log capture.
4. **Unverified release modelling.** `FleetScheduler.allocate` derives `clean` from
   `bench.release()`, which is better than the runner's old unconditional
   `release(clean=True)` — but with (1)–(3) unresolved, wiring it would make a
   verifiable refusal into an unverifiable success claim.

## Verification (no hardware, QEMU, or real coordinator acquisition)

RED before production edits — `tests/unit/test_runner_lifecycle_gate.py` against base:

```
5 failed, 3 passed in 3.35s
assert 7 == 75                      # fleet run spawned a real child, exit 7
'fleet_lifecycle_unavailable' in '==> ADMISSION REFUSED: lane_busy ...'   # wrong reason
'fleet_lifecycle_unavailable' in '==> ADMISSION REFUSED: ManifestError ...'
```

The first failure is the audit's blocker reproduced end-to-end: `main()` reserved a
lane, spawned a harmless child, and returned the child's status.

GREEN after the gate: **8 passed in 2.24s**. Coverage cases:
- refusal with an empty lane and with the lane already occupied (no capacity freed or
  consumed either way), for both `pytest` and `playwright` profiles;
- refusal without constructing the authority (store file absent afterwards);
- fleet `--dry-run` still exits 0 with no store and no child;
- non-fleet run keeps `output.log` content **and** propagates child exit 7.

Focused suite (real SQLite, coordinator doubles only):
`python3 -m pytest tests/unit/test_runner_lifecycle_gate.py
tests/unit/test_runner_manifest_authorization.py tests/unit/test_run_admission.py
tests/unit/test_run_admission_review_fixes.py tests/unit/test_fleet_scheduler.py -q`
→ **60 passed in 9.77s**.

Coverage (coverage 7.17.0 installed to an external scratch target; no project
dependency change), `--branch --include=scripts/run-profile.py` over the four runner
suites: file total **49%** (191/357 statements, 39/116 branches) — the file is a CLI
with render/collect paths that this milestone does not exercise.
**Added executable lines vs base: 12/12 covered (100%)**, computed by intersecting
`git diff -U0 d5e5fc3 -- scripts/run-profile.py` added line numbers with the coverage
JSON executed/missing sets.

Live CLI evidence (harmless profile, temp dirs, no coordinator):
```
fleet run   RC=75  STORE_EXISTS=no  RESDIR_EXISTS=no
fleet --dry-run  RC=0   DRY_STORE=no
```
The real (non-dry) fleet run created neither the store nor the results directory, so
the refusal happens before any local side effect, not merely before the spawn.

## Remaining work to enable fleet execution (not done here)

1. **Binding:** derive `(coordinator, place)` from the selected manifest resource and
   assert the profile's endpoint/env targets exactly that place; refuse unsupported
   profiles explicitly rather than running them under a foreign claim.
2. **Fixture ownership handoff:** pass the acquired place/identity into pytest (env or
   fixture contract) so `tests/conftest.py` verifies and reuses the runner's owner
   instead of acquiring again; add a test that the fixture does not re-acquire.
3. **Supervision:** tee-and-supervise in one path (log capture + `Lease.run`-style
   polling), renew on a heartbeat, and terminate the process group on fence/expiry
   **and prove it is gone** before releasing.
4. **Renewal failure is fatal:** a failed `renew()` must terminate and raise, not be
   suppressed until the TTL check.
5. **Release evidence:** `clean` must come from a verified coordinator release
   (`owner()` re-read) plus verified child teardown; unconfirmed ⇒ `CLEANUP_PENDING`
   and the lane stays excluded. Cancel `_compensate`'s "nothing held = clean" shortcut
   for ambiguous acquisition failures.
6. **Tests for (3)–(5)** with coordinator doubles and real SQLite: refused acquisition
   never spawns, fenced child is killed and proven dead, renewal failure terminates,
   unconfirmed release never frees capacity, and log/exit-status preservation under
   supervision.

Only after 1–6 should `require_supported_fleet_lifecycle` be relaxed, and that change
must land with those tests — not before them.

## Scope discipline

No hardware, QEMU, real coordinator acquisition, upstream PR, PR334 branch update,
force-push or card transition. Feature branch only (`pr/fleet-runner-lifecycle`).
# Runner manifest authorization milestone

Base: `f345f1b828a956a3d686f7f83b849872b8885be3` (PR334).
Branch: `pr/fleet-runner-authorization`. Prior report preserved below.

## Change
- Real FleetManifest membership and acquire/release permissions are checked even
  for explicit CLI targets. Observe-only manifests are refused.
- Resource aliases resolve to canonical manifest identity; declared shared
  conflicts are unioned with CLI domains, never replaced by them.
- Admission passes expected fleet/resource to authority; comparison is inside
  the reservation transaction before grant or replay, avoiding check/reserve races.
- Existing inference tests now use parse_manifest, not fictitious `.places` doubles.
- Nonfleet profiles remain supported: omission of --fleet-manifest still opts out.
  This is NOT mandatory global fleet enforcement or profile endpoint validation.

## Verification (no hardware, QEMU, or real coordinator acquisition)
RED before production edits: new real-schema tests **11 failed, 1 passed in 1.94s**;
failures were missing refusals and missing shared conflict domain.
GREEN same tests: **12 passed in 1.75s**.
Focused suite: **212 passed in 31.29s** using:
`python3 -m coverage run --branch --source=lib.run_admission,lib.fleet_authority -m pytest tests/unit/test_run_admission.py tests/unit/test_run_admission_review_fixes.py tests/unit/test_runner_manifest_authorization.py tests/unit/test_fleet_authority.py tests/unit/test_fleet_authority_aggregate.py tests/unit/test_fleet_capacity.py tests/unit/test_fleet_scheduler.py -q`
Coverage: run_admission **97%**, fleet_authority **86%**, total **87%**
(statement+branch). Added executable lines versus base: **20/20 covered (100%)**,
computed by intersecting git diff -U0 added line numbers with coverage JSON
executed/missing lines (17 admission, 3 authority).
Coverage installed in external scratch target because environment lacked it;
prepend that target to PYTHONPATH to reproduce. No project dependency change.
`git diff --check` passed. Full suite / hardware / QEMU: NOT RUN.

## Remaining three blockers — merge/benchmark recommendation remains withdrawn
1. Runner does not perform coordinator-owned acquisition or bind the selected
   manifest resource to the profile endpoint/environment.
2. Running subprocesses lack lease renewal, authority checks, and fencing/termination
   on ownership loss; plain tee_run is not scheduler supervision.
3. Runner asserts clean release without proven child teardown/coordinator release.

These lifecycle paths are deliberately unchanged. Next milestone must integrate
scheduler supervision; this change alone does not make production fleet runs safe.
No upstream PR, PR334 branch update, or card transition is part of this milestone.

---

PRTA-REVIVE / t_a4aa351d — REPORT
Consolidate bench locking into ONE machine-global lease
================================================================

Branch : pr/prta-revive-hw-dispatch
Fork   : felixfelix-bot/physical-router-test-automation
Head   : 6ceb9fe  (pushed, verified with ls-remote == local rev-parse)
Worktree: /home/c03rad0r/worktrees/prta-revive

Commits in this attempt
-----------------------
6ceb9fe  fix(test): keep pytest off the production bench lease; pin
         force_release semantics
28cbbe3  feat(bench): finish the lease consolidation — board/bench wrappers +
         legacy sweep
(two earlier commits from attempt 1, already on the fork: 94f3315 the lease
 itself, 435d72e the dispatch lane)

WHAT WAS DONE
-------------
There is now ONE lock mechanism: the flock + holder line on
~/.hermes/state/bench-mt3000.lock, implemented once in lib/bench_lock.py
(BenchLock) and exposed by scripts/hw-bench-lease and
scripts/mt3000-bench/bench-lock.sh. Everything that used to make its own lock
file delegates to it:

  Makefile:53  `hardware.lock` presence check  -> hw-bench-lease require
  lib/hardware_lock.py  /tmp/tollgate_hardware.lock -> BenchLock
  lib/router_lock.py  `routers.lock`           -> BenchLock
  esp32/  board-a/b/c.lock (per-worktree!)     -> BenchLock, board-scoped file
  mint-health/  `routers.lock`                 -> hw-bench-lease
  scripts/bench/bench.py  ~/upgrade-test/bench.lock -> BenchLock

The two mechanisms the card did not name were found by grepping for lock
creation sites; they were the same bug class in different directories.

The idle-gate primitive (lib/session_verify.check_balance_api) is wired in, not
duplicated: `hw-bench-lease exec --check-idle` runs it through an HTTP-only
router shim, refuses while a paying session is live, and fails closed when
/balance cannot be probed.

Stale handling: the flock is the authority, so a dead holder's lock is dropped
by the kernel and can never wedge the bench. A leftover holder line with no
flock refuses by default (rc 3) and needs an explicit --reclaim-stale. The
retired files (routers.lock/hardware.lock/tollgate_hardware.lock) are surfaced
by `status`/`exec` with their age — including ones in sibling worktrees — and
removable with `prune-legacy`; nothing reads them any more, so they cannot
block, and the sweep exists so an operator can see and clear them.

ACCEPTANCE EVIDENCE  -> evidence/prta-lease/ACCEPTANCE.md
--------------------------------------------------------
AC1 one mechanism: `git grep fcntl.flock` over shipped code returns four sites,
    all in lib/bench_lock.py. Every lock file created anywhere is a
    BenchLock/bench-lock file. 17 files reference the lease; none creates a
    competing one. Honest scope note included for the ONE remaining flock: the
    poller's $SPOOL/.poll.lock, which serialises cron ticks and grants no
    authority over the router.
AC2 second agent blocked: evidence/prta-lease/concurrent-run.log and
    board-concurrent-run.log — rc 2, "BENCH BUSY: ... locked by profile=<p>
    pid=<pid> purpose=<what> since=<when> task=<id> host=<host>", with the
    path and a how-to-proceed hint; guard agrees while held (rc 0) and after
    release (rc 1).
AC3 stale reclaimed: evidence/prta-lease/stale-reclaim.log — 93-day
    routers.lock/hardware.lock ignored (run proceeds), prune-legacy removes
    them dry-run-then-real without touching the lease, and a dead holder line
    refuses then reclaims with --reclaim-stale.

Release on failure (approach step 5) verified live: a child exiting 7
propagated rc 7 and the lease read `free` immediately after; the idle-gate
refusal path also releases (pinned by a unit test asserting status is free).

TESTS (all run with TMPDIR off /tmp)
------------------------------------
  tests/unit/test_bench_lease.py       31 passed  (was 30; +1 new)
  tests/unit/test_router_lock.py       22 passed  (with test_hardware_lock.py)
  tests/unit/test_bench_lock.py        } 82 passed total across the four
  tests/unit/test_hardware_lock.py     } lease suites
  tests/scenarios/test_two_router.py::TestRouterLockCoordination  2 passed
  make check-workflows                 PASS 24/24
  make lock-status                     wired end to end (root, mint-health, esp32)

REGRESSION FOUND AND FIXED (worth flagging to the reviewer)
-----------------------------------------------------------
`pytest tests/scenarios/test_two_router.py` failed, and the cause was not the
assertion that failed:

  * That test class runs with NO router, but called RouterLock() — which after
    the delegation is the PRODUCTION lease. Three scenario modules do this and
    conftest's hardware gate does not cover them, so a plain offline `pytest`
    run could seize the real bench lock and contend with a live window or a CI
    dispatch. That is the exact bug class this card exists to kill, reintroduced
    through the test suite.
    FIXED with a session-scoped autouse fixture in tests/conftest.py that points
    TOLLGATE_BENCH_LOCK at a temp file unless the caller opted into hardware
    work or set the variable itself. Verified both ways: 82 tests leave the
    production lease `free`; an explicit TOLLGATE_BENCH_LOCK control run uses
    that path instead.
  * It also asserted retired semantics ("force_release always wins"). flock
    forbids stealing a live lock by design, so the test now seeds a dead
    window's holder line, asserts BENCH STALE on acquire, then asserts
    force_release clears it and the next acquire succeeds.

Also fixed while verifying: the idle gate could fault OPEN if
check_balance_api ever raised (it would abort with a traceback rather than
refuse). Test written RED first — confirmed failing without the fix — then
green.

CAVEATS / NOT DONE
------------------
  * No CI run URL: triggering the fork workflow needs the token/runner this
    card's sibling t_30255dee owns, and the bench at 192.168.1.1 is not
    pingable from this host right now, so a dispatch would have nothing to test
    against. Nothing here claims a dispatch succeeded.
  * test_poe_power_cycle.py cannot be collected on this host at all:
    `ModuleNotFoundError: tollgate_lab`. Pre-existing (I did not touch that
    file); its RouterLock usage is covered by the conftest fixture regardless.
  * The allowlist entries I added to ~/.git-hooks/secret-allowlist.txt are
    OUTSIDE the repo and not committed. They suppress false positives where the
    markdown-table scan arms file-wide on a pre-existing "password" table row
    and then flags unrelated prose about the lease. A fresh clone on another
    machine will hit the same false positives when those docs change.
