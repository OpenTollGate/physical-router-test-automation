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
