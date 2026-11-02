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
