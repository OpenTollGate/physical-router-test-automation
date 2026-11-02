# PROGRESS — endpoint binding (requirement 1 + non-overlap half of 2)

Branch `pr/fleet-endpoint-binding`, worktree `~/worktrees/prta-endpoint-binding`,
base `237c12d39f22bcd8b8ebc2d8ebf7e1badf87ea3b` (PR #334 head).

- Re-read the seam myself before writing anything: `tests/conftest.py:346-415` is the
  place owner (`plan_venue` → `venue_mutex`, session-scoped, `pytest.exit(rc=4)` when
  the place is held), and `scripts/run-profile.py:229-245` refuses every fleet run.
  Both confirmed byte-identical to base at the end (`git diff --exit-code 237c12d --
  scripts/run-profile.py tests/conftest.py` clean). So the new layer only *predicts*
  the venue resolution; it never acquires.
- Proved the gap on the base code before designing: `evidence/.../binding-gap-probe.py`
  drives the real `plan_venue` with coordinator A / lane coordinator B / lane place
  X / venue place Y and prints `DEFECT CONFIRMED: 2 disagreement(s) — coordinator,
  place`. Saved log beside it.
- RED first: with the module absent the test file dies at collection
  (`ModuleNotFoundError: lib.fleet_binding`, log saved); then each rule was added
  branch by branch against its asserting test.
- Built `lib/fleet_binding.py`: `check_binding` refuses (a) coordinator disagreement
  (normalised `host:port`, refuses empty/unset either side), (b) place disagreement
  **including a `TOLLGATE_LABGRID_PLACE` override that retargets the lane**
  (`place_override_conflict`, refused not accepted), (c) modes that acquire no place
  plus the unit-only/parity auto-skip a manifest cannot tolerate, (d) protected
  dossiers, (e) fleet/lane/place ambiguity — 20 stable codes, `CODES` pinned by a test.
- Non-overlap: `module_io_audit()` AST-audits the module's own imports/symbols/calls
  (and is itself proved non-vacuous against rogue files); `LabgridBench.acquire/release`,
  `venue_mutex`, `bench_for_router` patched to explode and a full `check_binding` still
  returns; a second resolution for one place is refused `place_already_claimed` before
  any acquisition, while a same-owner retry is not.
- Mirror test: `venue_intent()` is pinned against the real `plan_venue` (I/O seams
  patched, as `test_venue_plan.py` does) across 6 env/scope cases plus the `ap-lan2`
  fallback, so the prediction cannot drift from the fixture it predicts.
- GREEN **91 passed**; coverage on the new module **100% statement and 100% branch**
  (276 stmts / 126 branches, 0 miss, 0 partial); neighbour suites **267 passed**.
  `git diff --check` clean. Full `tests/unit` has 2 failures + 9 errors that reproduce
  on untouched base (`test_cudy_flash`, `test_read_only_guard`, `test_hardware_lock`
  bench-lease fixture), plus `test_story_evidence` needing `PIL` — an environment gap.
- Requirement status: **1 MET as tested logic (not wired)**, **2 partially MET —
  non-overlap half only (handoff itself still absent)**, 3 & 4 already met as
  machinery, **5 still outstanding**. Five remain outstanding; the fail-closed gate
  stays closed, unchanged.
- Not done, by instruction: no wiring into `run-profile.py`, no `tests/conftest.py`
  change, no hardware/QEMU/coordinator/place, no inventory file read, no PR334 push,
  no merge, no force-push, no card transition.

---

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
