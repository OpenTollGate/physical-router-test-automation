PRTA-REVIVE t_a4aa351d — ACCEPTANCE EVIDENCE
branch pr/prta-revive-hw-dispatch @ 28cbbe3 (+ evidence refresh)
=================================================================

AC1 — only ONE lock mechanism remains; no other lock files are created.
-----------------------------------------------------------------------
The authority is the flock on ~/.hermes/state/bench-mt3000.lock held by
lib/bench_lock.BenchLock. Everything else delegates to it.

$ git grep -n "fcntl.flock" -- 'lib/*.py' 'scripts/*' 'scripts/**/*.sh' ':!tests/*'
lib/bench_lock.py:243:        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
lib/bench_lock.py:247:        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
lib/bench_lock.py:545:            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
lib/bench_lock.py:611:            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
  => the ONLY module that takes a resource flock. Four sites, one file.

$ git grep -nE "\.lock[\"']|lock_name|LOCK_PATH *=" -- 'lib/*.py' 'scripts/*' 'esp32/*.py' ':!tests/*'
esp32/conftest.py:67:  lock_path = ... f"esp32-{board_config.lock_name}.lock"   (BenchLock, board-scoped)
lib/bench_lock.py:61:  DEFAULT_LOCK_PATH = ~/.hermes/state/bench-mt3000.lock   (the lease)
scripts/mt3000-bench/bench-lock.sh:64: DEFAULT_LOCK_PATH=~/.hermes/state/bench-mt3000.lock (same file, shell client)
  => every lock file created in shipped code is a BenchLock/bench-lock file.
     The retired names appear only as data in LEGACY_LOCK_NAMES (the sweep).

$ git grep -ln "bench_lock\|hw-bench-lease" -- 'lib/*.py' 'scripts/*' 'Makefile' 'esp32/*' 'mint-health/*' ':!tests/*'
Makefile  esp32/boards.env  esp32/conftest.py  lib/bench_lock.py
lib/hardware_lock.py  lib/install_paths.py  lib/router_lock.py
mint-health/Makefile  scripts/bench/bench.py  scripts/fresh-flash.py
scripts/hw-bench-lease  scripts/hw-dispatch/*  scripts/install-path-e2e.py
scripts/mt3000-bench/bench-lock.sh  scripts/recovery/switch_tftp_recovery.py
  => 17 files, one mechanism (lib/bench_lock.py + its two CLIs).

RETIRED (the three the card names, plus two more found on the way):
  Makefile:53 `hardware.lock` presence check      -> hw-bench-lease require
  lib/hardware_lock.py /tmp/tollgate_hardware.lock -> BenchLock
  lib/router_lock.py `routers.lock`                -> BenchLock
  esp32/ board-a/b/c.lock (per-worktree, $(pwd)/../locks) -> BenchLock, board-scoped
  mint-health/ `routers.lock`                     -> hw-bench-lease
  scripts/bench/bench.py ~/upgrade-test/bench.lock -> BenchLock

SCOPE NOTE (honest): scripts/hw-dispatch/poll-queue.sh:56 keeps a
`flock -n 9` on $SPOOL/.poll.lock. That is NOT a hardware lock and does not
contradict AC1: it is a self-serialisation guard so two cron ticks of the
poller cannot overlap (it exits 0 on contention rather than blocking the
tick). It grants no authority over the router — the router authority is still
the one bench lease, taken further down the same script. Left in place
deliberately.

AC2 — a second concurrent agent is blocked, with a clear message.
-----------------------------------------------------------------
evidence/prta-lease/concurrent-run.log (bench) and
evidence/prta-lease/board-concurrent-run.log (ESP32 board), both produced by
the committed scripts against HEAD:

  agent B: ... exec --purpose ... -- echo SHOULD-NOT-RUN
  BENCH BUSY: the MT3000 bench is locked by profile=worker-heavy
    pid=3552228 purpose=PRTA-REVIVE-evidence-agent-A since=...
    task=- host=c03rad0r-DQ05proplus [...]
    (/home/c03rad0r/.hermes/state/bench-mt3000.lock).
    Wait for that run to finish, or kill its tree (pgrep -af 3552228) and retry.
  agent B rc=2 (want 2 = BENCH BUSY)
  VERDICT: second agent blocked with a named holder; lease released cleanly.

The refusal names the profile, pid, purpose, since, task and host, and says
how to proceed — that is the "clear message" AC.

AC3 — stale locks are detected and reclaimed.
---------------------------------------------
evidence/prta-lease/stale-reclaim.log, five scenarios:
  1. a 93-day routers.lock + hardware.lock in a sibling worktree exist on disk
     and the lease is FREE — the old files are not authority;
  2. `exec` RUNS despite them (they cannot block) while noting on stderr:
     "retired lock file (STALE, ignored): ... age=93.0d ... nothing reads it;
      prune with `hw-bench-lease prune-legacy`";
  3. `prune-legacy` removes them (dry-run first), refusing to touch the lease;
  4. the lease is untouched and still free afterwards;
  5. a STALE lease holder line (owner died mid-run) REFUSES by default
     (rc 3, holder named) and reclaims with --reclaim-stale (rc 0).

Why a stale lease can never wedge the bench: the flock is the authority, so
the kernel drops it when the holder dies. A leftover holder line without a
flock is metadata only, and recovery from it is explicit.

RELEASE ON FAILURE (approach step 5)
------------------------------------
`exec` wraps the child in try/finally; verified live: a child exiting 7
propagated rc 7 and the lease read "free" immediately afterwards. The idle
gate's refusal path returns before the child runs and also releases (pinned by
test_refuses_when_session_active asserting status is free afterwards).

TESTS
-----
tests/unit/test_bench_lease.py            31 passed
tests/unit/test_router_lock.py            22 passed (with test_hardware_lock.py)
tests/unit/test_bench_lock.py             }
    => 82 passed in total (the four lease suites), production lease still free
make lock-status                          wired end to end (root, mint-health, esp32)

REGRESSION FOUND AND FIXED WHILE VERIFYING (not in the original plan)
---------------------------------------------------------------------
`pytest tests/scenarios/test_two_router.py` failed on
TestRouterLockCoordination::test_force_release_allows_new_session, and the
failure was more than a stale assertion:

  1. That class runs with NO router (the rest of the file skips without
     TOLLGATE_SECONDARY_ROUTER_HOST), yet it called RouterLock() — which, after
     the delegation, is the PRODUCTION lease at
     ~/.hermes/state/bench-mt3000.lock. Three modules do this
     (test_two_router.py, test_labgrid_venue.py, test_poe_power_cycle.py), and
     conftest's hardware gate does not cover them. A plain offline pytest run
     could therefore take the real bench lock and contend with a live window or
     a CI dispatch.
  2. The test asserted the retired semantics ("force_release always wins"), which
     flock deliberately forbids: a live lock cannot be stolen, only a dead
     window's leftover holder line can be cleared.

Fixes:
  * tests/conftest.py gains a session-scoped autouse `_hermetic_bench_lease`
    fixture that points TOLLGATE_BENCH_LOCK at a per-session temp file unless
    the caller opted into hardware work (TOLLGATE_USE_HARDWARE_LOCK /
    --lock-phase) or set TOLLGATE_BENCH_LOCK itself. Verified: the full unit
    suite (82 tests) and the scenario lock tests leave the production lease
    free, and an explicit TOLLGATE_BENCH_LOCK still wins (control run).
  * the test now exercises what force_release actually guarantees: seed a dead
    window's holder line -> acquire refuses BENCH STALE -> force_release clears
    it -> the next acquire succeeds. The live-lock half is already pinned by
    test_router_lock.py::test_live_lock_cannot_be_stolen_by_force_release.

