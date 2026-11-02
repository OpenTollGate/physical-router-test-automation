# PROGRESS — supervised execution machinery

Branch `pr/fleet-supervised-exec`, worktree `~/worktrees/prta-supervised-exec`,
base `ce14c790479710d901e96ce5e2948b69109f2503` (PR #334 head).

- Read `~/reports/reviews/prta-334-runner-safety.md`, then checked the promoted
  manager finding in `tests/conftest.py:346-415` myself: the router fixture already
  does coordinator-owned place acquisition (`plan_venue` + `venue_mutex`, held for the
  session, `pytest.exit(rc=4)` if the place is taken). Confirmed — so the runner must
  **not** acquire. Supervision covers host budget only.
- RED first: `tests/unit/test_supervised_run.py` → **16 failed, 2 passed**. The two
  passes are the characterisation pair proving today's defect: the real `tee_run` ran a
  0.5 s child under a 0.2 s lease with **0 renew calls**, expiry unchanged, and the lane
  then quarantined `lease_expired` — the audit's finding as an executable test.
- Built `lib/supervised_run.py`: heartbeat renewal, fatal-on-renewal-failure
  (SIGTERM→SIGKILL group, exit 76), fencing/authority-loss kill (exit 77), and
  `CONFIRMED`/`UNCONFIRMED` teardown evidence probed with `killpg(pgid, 0)`.
- Fixed two wedges found while writing the tests: unbounded drain on a grandchild-held
  pipe, and reap-before-probe ordering (a zombie group answers `killpg(pgid, 0)`).
- Fixed two flaky tests that raced the child's startup instead of the behaviour under
  test (wait for the child's pid file before fencing / failing renewal).
- GREEN **29 passed**, stable 5× and under random ordering. Focused regression suite
  **252 passed**. Coverage **97%** (statement+branch) on the new module.
  `git diff --check`, `ruff check`, `ruff format --check` all clean.
- Requirement probe run and saved: `evidence/prta-supervised-exec/` — 17 renewals across
  a 0.3 s TTL, `RenewalFailed exit=76`, `AuthorityLost exit=77`, unconfirmed teardown
  reported as such.
- Gate untouched: `scripts/run-profile.py` byte-identical to base; fleet execution still
  refuses with exit 75 / `fleet_lifecycle_unavailable`.
- Remaining before the gate may relax: (1) manifest-resource → profile endpoint/env
  binding, (2) re-scoped fixture handoff (runner passes host reservation + run identity;
  fixture stays sole place owner), (5) coordinator-release evidence so `clean` is
  justified. See REPORT.md for the per-requirement table.
- Not done, by instruction: no hardware/QEMU/coordinator acquisition, no place taken, no
  PR334 push, no force-push, no upstream PR, no merge, no card transition.

---

# Runner lifecycle gate — bounded milestone

---

# Runner lifecycle gate — bounded milestone

- Worktree `~/worktrees/prta-runner-lifecycle`, branch `pr/fleet-runner-lifecycle`,
  base `d5e5fc30a8f0bd27ea3b5f26adcd3b38438eb7b2`.
- Read `~/reports/reviews/prta-334-runner-safety.md`; inspected
  `FleetScheduler`/`Lease`/`LabgridBench.for_run` and `tests/conftest.py` venue fixture
  before deciding. No scheduler code reused or duplicated.
- RED first: new gate tests `5 failed, 3 passed`; the primary failure was the audit's
  blocker reproduced end-to-end (`assert 7 == 75` — lane reserved, real child spawned,
  child status returned).
- Shipped the permitted fail-closed fallback: fleet execution refused with exit 75 /
  `fleet_lifecycle_unavailable` before store creation, results dir, reservation or spawn.
  Authorization fix, read-only dry-run and non-fleet log/exit behaviour preserved.
- GREEN `8 passed`; focused suite `60 passed`. Added executable lines: 12/12 covered.
  File-level runner coverage 49% (CLI render/collect paths out of scope).
- Remaining to enable fleet execution: place binding, fixture ownership handoff,
  renewal/fencing-proof teardown, release evidence, plus tests — listed in REPORT.md.
- Not done, by instruction: no hardware/QEMU/coordinator acquisition, no PR334 push,
  no upstream PR, no card transition, no main merge.

---

# Runner authorization bounded milestone

- Isolated worktree created at exact PR334 base f345f1b828a956a3d686f7f83b849872b8885be3.
- Read safety audit and executable probe; reproduced authorization failures with
  real loaded FleetManifest fixtures before implementation (11 failed / 1 passed).
- Implemented membership, permission, domain union and transactional lane identity check.
- Verified 212 focused tests pass; changed executable line coverage 100%.
- Lifecycle/coordinator/cleanup repairs deliberately deferred: see REPORT.md.
- Feature branch prepared for parent integration, not a competing upstream PR.

---

# PROGRESS — t_a4aa351d (Consolidate bench locking into ONE machine-global lease)

Worktree: /home/c03rad0r/worktrees/prta-revive  branch pr/prta-revive-hw-dispatch
Head: 6ceb9fe (PUSHED to fork, ls-remote verified)

## Attempt 1 (run 21, timed out) — recovered
- 94f3315 lease + 435d72e dispatch lane already pushed.
- ~730 lines uncommitted work recovered, reviewed, verified, committed as 28cbbe3.

## Attempt 2 (run 26) — done
- 28cbbe3 finish consolidation: esp32 board leases, mint-health, bench.py,
  poll-queue CHECKOUT bug, legacy sweep + prune-legacy, idle-gate fail-closed
  fix (RED-first test), evidence logs. PUSHED.
- 6ceb9fe regression fix: pytest could take the PRODUCTION lease (3 scenario
  modules bypass conftest's gate) + force_release semantics test pinned.
  PUSHED.
- Verified: 82 lease unit tests, scenario lock tests, make check-workflows 24/24,
  make lock-status end to end, production lease still free after test runs.
- Evidence: evidence/prta-lease/{ACCEPTANCE.md,concurrent-run.log,
  stale-reclaim.log,board-concurrent-run.log}
- REPORT.md written.

## Remaining (for the sibling card t_30255dee, not this one)
1. Trigger the dispatch workflow and capture a run URL (needs runner/token; bench
   not reachable from this host — 192.168.1.1 not pingable).
2. Nothing else outstanding for this card's acceptance criteria.
