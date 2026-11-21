# Resume audit reporting corrections (2026-11-03)

Base `6ccb3df6e7d8dfc8b554ea25473b2ec8d1f9299d`, fetched from
`fork/pr/fleet-consolidated`; isolated branch `pr/prta-reporting-corrections`.

- CLI now says `exported/unclaimed; eligibility unproven`; documentation limits
  that observation to coordinator acquisition, not authority leases/admission.
- Manifest/docs correct the missing-proxy diagnosis: installed labgrid uses SSH
  forwarding; authentication/routing/forwarding remain unverified.
- Both benchmarks label output diagnostic-only, not Gate E or production
  scheduler qualification. Real-workload returns exit 1 and withholds comparison
  on any failed subprocess. No admission/idle implementation changed.

## Local verification (doubles only)

RED before implementation: **9 failed, 1 warning in 0.24s**. All six possible
failed job positions (three serial, three parallel) reproduced the erroneous
zero exit; missing diagnostic banners and CLI label also failed. Warning was
an unregistered existing `story` mark with hardware conftest bypassed; GREEN
explicitly registers it.

Focused command, using Hermes venv Python and coverage 7.17.1 in manager scratch:

```sh
python -m coverage run --source=scripts,lib -m pytest \
  --confcutdir=tests/unit -o addopts='' \
  -o 'markers=story: story traceability' \
  tests/unit/test_benchmark_reporting.py tests/unit/test_fleet_capacity_cli.py \
  tests/unit/test_fleet_capacity.py tests/unit/test_reachable_unclaimed.py \
  tests/unit/test_reservable_is_not_drivable.py \
  tests/unit/test_bench_fleet_manifest.py -q
```

Actual output:

```text
144 passed in 3.00s
Name                             Stmts   Miss  Cover
----------------------------------------------------
lib/fleet_capacity.py              684     36    95%
scripts/bench-real-workload.py      31      0   100%
scripts/bench-throughput.py         52      4    92%
scripts/fleet-capacity.py          222     48    78%
----------------------------------------------------
TOTAL                              989     88    91%
```

Changed executable statements: **7/7 = 100%**, checked against coverage JSON:
real-workload lines 17,18,23,35–37 and throughput line 62. CLI line 189 is part of
a covered multiline rendering expression (not a distinct coverage statement);
the new rendered string is asserted. Library changes are docstring-only.
Whole CLI file coverage is 78%, not an 80% whole-file claim.
`git diff --check`: exit 0, no output.

Coverage was missing and installed with pip --target into manager scratch, not
the shared venv. Unfiltered JSON reporting hit an unrelated pre-existing
Python-3.11-incompatible f-string in `lib/cloud_lab/worker/conwrt_deploy.py`;
reporting only the four touched production files succeeded.

No hardware scripts ran against devices: tests replaced every subprocess with
doubles. No coordinator/device probes, acquisition, credential changes or VM
launches; all hardware remained hands-off. The live integration expectation was
updated but **NOT RUN**. No scheduler qualification, tunnel success or throughput
gain is claimed. Existing diagnostic limitations (discarded workload output,
direct serial bypass, unchecked place-hold release) remain documented.
Shared checkout/untracked evidence untouched; push only this feature branch,
no new upstream PR or merge. Historical reports below are retained as history.

---

# Requirement 1 + the non-overlap half of 2 — endpoint binding, as tested pure logic

Base: `237c12d39f22bcd8b8ebc2d8ebf7e1badf87ea3b` (PR #334 head).
Branch `pr/fleet-endpoint-binding`, worktree `~/worktrees/prta-endpoint-binding`.
Read first: `REPORT.md` → "Remaining work to enable fleet execution" (items 1–6).

## Outcome

`lib/fleet_binding.py` (new, 276 statements) answers the question nothing in the
repo answers today: **is the manifest lane the runner is about to reserve host
budget for the same physical DUT the pytest fixture is about to own?**

It is pure logic over injected inputs — a manifest object, an inventory object, and
an env mapping. No coordinator contact, no `labgrid-client`, no inventory file, no
env read, and **no acquisition**. The place owner is, and stays,
`tests/conftest.py:346-415` (`plan_venue` → `venue_mutex`); the runner must not
acquire, so this layer only *predicts* what that resolution will target and refuses
when agreement cannot be shown.

**Nothing is wired.** `scripts/run-profile.py` and `tests/conftest.py` are
byte-identical to base (`git diff --exit-code 237c12d -- scripts/run-profile.py
tests/conftest.py` is clean). Fleet execution still refuses with
`exit 75` / `fleet_lifecycle_unavailable`. That refusal is unchanged, deliberately.

## The defect this closes, on the code as it is

`evidence/prta-endpoint-binding/binding-gap-probe.py` (log beside it) drives the
real `plan_venue` with an inventory whose coordinator is `coord-a` and a manifest
lane whose coordinator is `coord-b` on place `x1860-2`, while the profile/env
resolves place `x1860-1`:

```
manifest fleet coordinator : coord-b.example:20408
inventory coordinator      : coord-a.example:20408
manifest lane place        : x1860-2
venue would acquire place  : x1860-1
acquisition-capable venue  : True

DEFECT CONFIRMED: 2 disagreement(s) — coordinator, place
```

Nothing on the current path compares those two statements, so that run would
reserve host budget for one DUT and drive another. `check_binding` refuses both
(`coordinator_address_mismatch`, `place_override_conflict`).

## Refusals, each with a stable code

Codes are an API: `CODES` is pinned by a test, `BindingRefused.code` is also
`.reason` (same shape as `lib.run_admission.AdmissionRefused`) so a call site can
forward it straight into an admission refusal.

| Rule | Code | What it catches |
|---|---|---|
| (a) manifest fleet `coordinator.address` ≠ inventory `coordinator.address` | `coordinator_address_mismatch` | the lane's budget is on fleet B's coordinator while the fixture acquires from A |
| (a) either side empty/unset | `coordinator_address_missing` | an unknown coordinator is never assumed to be the right one |
| (b) manifest place ≠ the place the venue resolution would target | `place_mismatch` | the two statements name different DUTs |
| (b) `TOLLGATE_LABGRID_PLACE` names a different place than the lane | `place_override_conflict` | **an env override may never retarget a reserved lane** — this is the refusal the requirement demands, not a warning |
| (c) mode acquires no place (`direct` / `legacy` / xdist worker / auto with unmet preconditions) | `venue_takes_no_place` | with a manifest, no place owner means the host-budget reservation is meaningless |
| (c) unit-only / parity-only session would auto-skip the venue | `fixture_would_skip_place` | conftest would own no place while the runner reserved a DUT |
| (d) `protected: true` dossier | `resource_protected` | refused before the place comparison, so the operator is told the real reason |
| (e) several fleets / several places / one place claimed by two dossiers | `fleet_ambiguous`, `lane_ambiguous`, `place_ambiguous` | a lane must name exactly one DUT |
| — | `manifest_missing`, `owner_missing`, `fleet_unknown`, `resource_not_authorized`, `resource_kind_unsupported`, `venue_place_unresolvable`, `venue_address_missing`, `profile_endpoint_mismatch`, `profile_endpoint_unknown`, `place_already_claimed` | the rest of the identity chain |

Precedence is fixed and tested: manifest identity → coordinator → venue resolution
→ place (including the override) → profile endpoint → claims. The first violated
rule wins, so a log line is deterministic when several things are wrong at once.

Coordinator comparison normalises `host:port` (case, trailing dot, IPv6 brackets,
missing port → labgrid's `20408`, zero-padded port) but refuses an empty,
host-less or non-TCP-port address. A different *port* is a different coordinator.

## Non-overlap, proved rather than asserted

| Test | What it proves |
|---|---|
| `test_layering_audit_finds_no_io_or_acquisition` | `module_io_audit()` parses this module's own AST; the module-allowlist, project-symbol allowlist and forbidden-call list are all empty |
| `test_the_only_project_import_is_the_pure_mode_resolver` | the sole `lib.*` import is `resolve_venue_mode`; `venue_mutex` / `bench_for_router` / `LabgridBench` / `load_inventory` / `hardware_lock` appear nowhere in executable code |
| `test_check_binding_never_calls_the_place_lifecycle` | `LabgridBench.acquire`, `.release`, `venue_mutex`, `bench_for_router` are patched to `AssertionError`; a full `check_binding` still returns a `Binding` |
| `test_a_second_resolution_for_the_same_place_from_another_run_overlaps` | run-1 binds, then run-2 for the same place is refused `place_already_claimed` **before** anything is acquired |
| `test_two_runs_on_different_places_do_not_overlap` | two lanes on two places coexist |
| `test_a_retry_of_the_same_run_is_not_an_overlap` | the same owner restating its claim is not contention |
| `test_the_audit_detects_a_violation` / `..._flags_an_unlisted_*_import` | the audit is not vacuous: a rogue file importing `subprocess`, calling `venue_mutex`, `open` or `subprocess.run` is reported |

Overlap is made observable without acquiring anything: `Binding.claim` is a
`PlaceClaim(place, owner)` a caller records, and `already_claimed=` / `find_overlaps`
turn "does a second resolution collide with the first" into a pure predicate.
Owner-less claims never overlap (that would refuse every retry) — and
`check_binding` never emits one, because `owner_missing` refuses first.

## The mirror (this is what keeps the prediction honest)

`venue_intent()` re-implements the *decision rules* of `plan_venue` + conftest
(xdist → legacy; auto + unit/parity → auto-skip; direct/legacy → legacy; auto with
unmet preconditions → legacy; explicit place; address match; `ap-lan2` fallback).
A pure prediction is only safe if it cannot drift, so
`TestVenueIntentMirrorsPlanVenue` runs the **real** `plan_venue` — patched only at
its I/O seams (`inventory_exists`, `_default_client_bin`, `load_inventory`),
exactly as `tests/unit/test_venue_plan.py` does — across six env/scope combinations
and asserts the two agree on `owns_place`, place **and** dossier, or that both
refuse. `test_default_place_fallback_matches_the_planner` pins the `ap-lan2`
fallback.

## How a later change calls it at the conftest/runner seam

Not done here. When the gate is relaxed, the seam is **one call in
`scripts/run-profile.py::require_supported_fleet_lifecycle`**, before
`FleetAuthority.__init__` mints the store and before any reservation:

```python
# scripts/run-profile.py — inside require_supported_fleet_lifecycle, replacing
# the unconditional raise for the profiles this binds successfully.
from lib.fleet_binding import BindingRefused, check_binding
from lib.fleet_capacity import load_manifest
from lib.lab_inventory import load_inventory

manifest = load_manifest(args.fleet_manifest)          # I/O: runner side, not binding
try:
    binding = check_binding(
        manifest=manifest,
        inventory=load_inventory(),                    # the very inventory plan_venue loads
        environ=env,                                   # the merged profile env this run exports
        fleet_id=args.fleet,
        resource_id=args.resource,
        lane_id=args.lane,
        owner=run_id,
        profile_endpoint=env.get("TOLLGATE_SSH_HOST", ""),
        session_scope=_scope_from_testpaths(profile),   # "unit" / "parity" / "full"
        xdist_worker=bool(env.get("PYTEST_XDIST_WORKER")),
        operator_host=env.get("TOLLGATE_SSH_HOST") or None,
    )
except BindingRefused as exc:
    raise AdmissionRefused(exc.code, exc.detail) from None
child_env.update(binding.as_env())   # TOLLGATE_VENUE=labgrid + TOLLGATE_LABGRID_PLACE=place
```

`binding.as_env()` is the reason conftest needs **no change**: exporting
`TOLLGATE_VENUE=labgrid` + `TOLLGATE_LABGRID_PLACE=<place>` makes the venue decision
*forced and explicit*, so the fixture cannot fall back to the legacy direct path
underneath a reservation, and cannot auto-skip on a unit-only session. `tests/conftest.py`
stays the sole place owner; the runner still acquires nothing.

## Test / coverage evidence

Commands run from `~/worktrees/prta-endpoint-binding`. The project venv
(`~/repos/physical-router-test-automation/.venv`) has pytest 9.1.1 but no
`pytest-cov`, so coverage ran in a cov-capable venv (`~/worktrees/t_8009b9c8/.venv`,
identical CPython 3.12.3) with the project site-packages on `PYTHONPATH`. Plain
runs used the project venv.

- `tests/unit/test_fleet_binding.py` — **91 passed** (project venv and cov venv).
- Coverage on the new module, statements **and** branches:
  `lib/fleet_binding.py 276 stmts, 0 miss, 126 branch, 0 partial → 100%`
  (`evidence/prta-endpoint-binding/coverage.log`).
- Neighbour suites (venue plan/mutex, fleet capacity/authority/safety, supervised
  run, labgrid venue/ownership) — **267 passed**.
- RED first: with the module removed the test file fails at collection with
  `ModuleNotFoundError: No module named 'lib.fleet_binding'`
  (`evidence/prta-endpoint-binding/red-missing-module.log`). Each rule was also
  written as an asserting test before its branch existed.
- Full `tests/unit` at this branch: **1738 passed, 7 skipped, 3 xfailed, 2 failed,
  9 errors**. The failures/errors are **pre-existing at base** and reproduce in the
  untouched `~/worktrees/fleet-consolidated` (base check): `test_cudy_flash.py::
  test_no_credential_literal_leaks_into_the_new_sources`,
  `test_read_only_guard.py::test_reads_still_work_in_read_only_mode`, and the
  `test_hardware_lock.py` fixture errors (machine-global bench lease state). Also
  `tests/unit/test_story_evidence.py` needs `PIL`, absent from both venvs — an
  environment gap, not a code change.
- `git diff --check` clean. `ruff check` on the three new files: one `UP042`
  (`Refusal(str, enum.Enum)`), matching the pre-existing `Reason`/`State`/
  `ResourceKind` in `lib/fleet_capacity.py`; CI does not lint `lib/` for `UP`.

## Requirement status after this milestone

Stated against the shipped runner path (nothing is wired), as the base REPORT does:

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Manifest resource → profile endpoint/env **binding**; refuse unsupported profiles | **MET as tested logic — not wired** | `lib/fleet_binding.check_binding` refuses all of (a)–(e) plus profile-endpoint disagreement and resource-kind/fleet/lane ambiguity; `Binding.as_env()` derives the endpoint/env. The runner does not call it yet. |
| 2 | Fixture ownership handoff (re-scoped: runner passes host-budget reservation + run identity; fixture stays sole place owner) | **PARTIALLY MET (non-overlap half only) — not wired** | Proved: the binding layer acquires nothing, the fixture remains sole owner, and a second resolution for one place is detectable pre-acquisition (`PlaceClaim`/`find_overlaps`/`place_already_claimed`). **Absent:** the handoff itself — passing `binding.claim`/run identity into the pytest run and having the fixture verify it reuses that owner. |
| 3 | Supervision | **MET (machinery, tested)** — not wired | unchanged from base |
| 4 | Renewal failure is fatal | **MET (machinery, tested)** — not wired | unchanged from base |
| 5 | Release evidence: coordinator-release half | **OUTSTANDING** | nothing here; still no `owner()`/bench re-read justifies `clean` |

**Five outstanding items remain five:** 1 and the re-scoped 2 now have their tested
logic, 3 and 4 were already met as machinery, and **5 is untouched**. The gate stays
closed because 1 and 2 are not wired and 5 has no implementation at all.

## Files

- `lib/fleet_binding.py` (new, 276 statements)
- `tests/unit/test_fleet_binding.py` (new, 91 tests)
- `evidence/prta-endpoint-binding/binding-gap-probe.py` + `.log` (defect proved on base code)
- `evidence/prta-endpoint-binding/red-missing-module.log`, `coverage.log`
- `REPORT.md`, `PROGRESS.md`

## Not done, by instruction

No wiring into `scripts/run-profile.py`; `tests/conftest.py` behaviour untouched (both
byte-identical to base); `run-profile.py`'s fail-closed gate intact. No hardware, QEMU,
real coordinator acquisition, labgrid place taken, or inventory file read in any test.
No PR #334 branch change, no merge, no force-push, no card transition.

---

# Supervised execution — machinery built and tested, gate still closed

Base: `ce14c790479710d901e96ce5e2948b69109f2503` (PR #334 head).
Branch: `pr/fleet-supervised-exec`, worktree `~/worktrees/prta-supervised-exec`.
Read first: `~/reports/reviews/prta-334-runner-safety.md`.

## Outcome

`lib/supervised_run.py` is a drop-in replacement for `scripts/run-profile.py::tee_run`
— same signature (`command, cwd, env, log_path, dry_run → int`), still streams to
stdout, still writes `output.log`, still returns the child's exit status — that
additionally keeps the authority lease honest while the child runs.

**The production gate is untouched.** `scripts/run-profile.py` has zero changes on
this branch (`git diff --stat` lists only the new module, its test and this evidence
dir). Fleet execution still refuses with `exit 75` / `fleet_lifecycle_unavailable`.
Machinery is built and tested; it is **not wired**.

## What the module does

| Requirement | Behaviour |
|---|---|
| (a) heartbeat | `FleetAuthority.renew` every `heartbeat_s` (default `ttl_s/3`) while the child runs |
| (b) renewal failure = FATAL | SIGTERM → grace → SIGKILL the process group, then raise `RenewalFailed` (exit **76**). Never suppresses and waits for TTL |
| (c) fencing | `renew` refusal (`Fenced`/`NotGranted`/`UnknownRun`) → terminate group, raise `AuthorityLost` (exit **77**). Pre-spawn `assert_authority_valid` also catches superseded fence, closed allocation and already-expired lease |
| (d) honest cleanup | after reaping, `killpg(pgid, 0)` must say the group is gone. `Outcome.cleanup` is `CONFIRMED`/`UNCONFIRMED`; `clean` is only `True` when confirmed, so the caller records `CLEANUP_PENDING` instead of claiming clean |

Child is spawned with `start_new_session=True`, so `pgid == pid` and there is a group
to kill. Teardown reaps **before** the final liveness probe, because an unreaped
zombie still answers `killpg(pgid, 0)` — the mirror-image dishonesty of claiming
clean without proof.

Two bounds that prevent a wedged lane:

- output draining stops `drain_after_exit_s` (default 1 s) after the child exits, so a
  surviving grandchild holding the pipe cannot hold the runner open;
- a group left alive by a exited child is killed and *re-probed*; unproven ⇒
  `UNCONFIRMED`.

## Scope: supervision is host budget only — no place acquisition

This module does **not** reserve, activate, release or touch a labgrid place. Place
ownership stays exactly where it is: `tests/conftest.py` (router fixture) already calls
`plan_venue` + `venue_mutex`, holds the place and hardware lock for the session, and
`pytest.exit(rc=4)` when the place is held by someone else. A runner-side acquisition
would be a **second owner for one DUT** — the double-ownership bug, not a fix for it.

Enforced structurally, not by prose: `test_supervision_does_not_import_labgrid_or_acquire_anything`
parses the module's AST and asserts it imports nothing named `labgrid*` and calls none
of `try_reserve/acquire/activate/release/complete/abort/fence`. The only authority
calls it makes are `renew` and `allocation` (both asserted, and a `__getattr__`
watcher fails the test on any other attribute).

## RED → GREEN

RED first, `tests/unit/test_supervised_run.py` before the module existed:
**16 failed, 2 passed**. The two passes are the characterisation pair:
`test_plain_tee_run_renews_nothing_and_lets_the_lease_expire` runs the *real*
`tee_run` for 0.5 s against a 0.2 s lease and asserts `renew` was called **0**
times, the expiry timestamp unchanged, then `expire()` quarantines the lane with
`lease_expired` while the child's output log says it finished — the audit's finding
reproduced in a test. That test must keep passing: it is the record of the defect,
and it would flip the moment anyone wires supervision into `tee_run` without the
heartbeat.

GREEN: **29 passed in 10.58s** (file alone, `-p no:randomly`), stable over 5
consecutive runs and also green under random ordering.

Focused regression suite (runner + fleet authority + admission + scheduler):
**252 passed in 31.77s**.

```
python3 -m pytest tests/unit/test_supervised_run.py tests/unit/test_runner_lifecycle_gate.py \
  tests/unit/test_run_admission.py tests/unit/test_run_admission_review_fixes.py \
  tests/unit/test_runner_manifest_authorization.py tests/unit/test_runner_authorization_integration.py \
  tests/unit/test_fleet_authority.py tests/unit/test_fleet_authority_aggregate.py \
  tests/unit/test_fleet_scheduler.py tests/unit/test_fleet_capacity.py -q
```

## Coverage

`coverage 7.17.0`, installed to an external scratch target (no project dependency
change; prepend that target to `PYTHONPATH` to reproduce):

```
python3 -m coverage run --branch --source=lib.supervised_run -m pytest \
  tests/unit/test_supervised_run.py -q -p no:randomly
python3 -m coverage report -m

Name                    Stmts   Miss Branch BrPart  Cover   Missing
-------------------------------------------------------------------
lib/supervised_run.py     221      2     52      5    97%   233->238, 246->251, 428->437, 444->413, 452-453
TOTAL                     221      2     52      5    97%
```

**97% (statement + branch) on the new module**, above the 80% bar. The residual
misses are `__all__` bookkeeping and defensive branches reachable only through a
`Popen` that refuses to start.

`git diff --check` clean; `ruff check` and `ruff format --check` clean on both new
files.

## The five enabling requirements — status at this SHA

The five come from `REPORT.md` "Remaining work to enable fleet execution"
(items 1–5; item 6 is their tests). Status is stated against *the shipped runner
path*, not against the new library:

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Manifest resource → profile endpoint/env **binding**; refuse unsupported profiles | **NOT MET** | Nothing derives `(coordinator, place)` from the selected manifest resource, and nothing asserts the profile's `TOLLGATE_SSH_HOST`/endpoint targets that place. Untouched by this milestone. |
| 2 | **Fixture ownership handoff** so the fixture reuses the runner's owner instead of acquiring again | **NOT MET (and deliberately not attempted)** | The fixture still acquires the place itself (`tests/conftest.py:381-400`). The promoted finding stands: the fixture is the *correct* owner of the place, so the runner must not acquire. That makes requirement 2 as originally worded **obsolete** — it should be re-scoped to "runner passes host-budget reservation + run identity to the fixture; fixture remains sole place owner". No handoff mechanism exists yet, so the requirement is open either way. |
| 3 | **Supervision**: tee + renew on heartbeat, terminate on fence/expiry, prove it is gone | **MET (machinery, tested)** — not wired | Requirements probe: child 1.0 s under a 0.3 s TTL → **17 renewals**, lane still `active`, `expire()` returns 0. Fence mid-run → `AuthorityLost`, store `fenced`, child killed. Teardown proven by `killpg(pgid, 0)`. |
| 4 | **Renewal failure is fatal**: terminate and raise, not suppressed until TTL | **MET (machinery, tested)** | Probe: `RenewalFailed exit=76 cleanup=confirmed terminated=True` at the first bad beat, well before the TTL. |
| 5 | **Release evidence**: `clean` from verified teardown; unconfirmed ⇒ `CLEANUP_PENDING` | **PARTIALLY MET** | Child-teardown half is done and returns `CONFIRMED`/`UNCONFIRMED` (`clean` only true when confirmed). The **coordinator-release half is absent**: nothing re-reads `owner()`/a bench claim, so a clean release still cannot be justified from coordinator evidence. `run-profile.py`'s `release(clean=True)` call site remains unproven and unchanged. |

Reproduce the requirement probe: `PROBE_DIR=$(mktemp -d) python3
evidence/prta-supervised-exec/requirements-probe.py`; saved output in
`evidence/prta-supervised-exec/requirements-probe.log`.

### Net effect on the gate

**Zero of the five are fully met on the production path**, so
`require_supported_fleet_lifecycle` stays exactly as it is. What changed is that
requirement 3 and requirement 4's supervision defects no longer need designing —
they have an implementation with tests. Requirements 1, the re-scoped 2, and 5's
coordinator half are the remaining work before the refusal may relax.

## Files

- `lib/supervised_run.py` (new, 221 statements)
- `tests/unit/test_supervised_run.py` (new, 29 tests)
- `evidence/prta-supervised-exec/requirements-probe.py`, `.../requirements-probe.log`
- `REPORT.md`, `PROGRESS.md` (updated)

## Not done, by instruction

No hardware, QEMU, real coordinator acquisition, labgrid place taken, or fleet
benchmark. No PR #334 branch change, no forced push, no upstream PR, no merge, no
card transition. `scripts/run-profile.py` byte-identical to base.

---

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


# The blanket refusal is replaced by the real supervised lifecycle

Base: `ed687b229006accd96b141a6dc434c750454c271` (PR #334 head).
Branch `pr/fleet-runner-integration`, worktree `~/worktrees/prta-runner-integration`.
Read first: `~/reports/reviews/prta-334-runner-safety.md` (the audit whose four
findings this closes).

## Outcome

`scripts/run-profile.py` no longer refuses every fleet run with
`exit 75 / fleet_lifecycle_unavailable`. A run that is **fully authorized, bound
to the venue the fixture will own, admitted against real host budget, and
supervised** now executes — and a run that is missing any of those still refuses
with exit 75 and touches nothing.

The refusal did not disappear; it moved to where the evidence is. Every refusal
below happens **before** a store is minted, a results directory is created, or a
child exists:

| gate | refusal code | exit |
|---|---|---|
| manifest does not grant `acquire` **and** `release` | `action_not_permitted` | 75 |
| fleet/resource not declared in the manifest | `fleet_not_authorized` | 75 |
| binding disagrees on coordinator or place | `binding_*` | 75 |
| venue mode that acquires no place | `venue_takes_no_place` | 75 |
| claim/environment handoff disagreement | `claim_*` | 75 |
| budget/lane admission refused | `admission_*` | 75 |
| lease renewal failed mid-run | `renewal_failed` | **76** |
| authority lost mid-run | `authority_lost` | **77** |

76 and 77 are supervision's own codes and are deliberately distinct from a test
verdict: `main()` returns them before collect/render/summary, so a supervision
failure can never be read as "tests passed" or "tests failed".

## The four audited defects

The audit's probe (`~/reports/reviews/prta-334-safety-probe.py`) is a
straight-line script. Against a fixed tree it stops at its first measurement —
`admission_from_args(...)` now raises `AdmissionRefused` — so it can no longer
report defects 2-4 at all. `evidence/prta-runner-integration/per-defect-probe.py`
measures the same four facts, each reached independently, so the result is
readable instead of short-circuited. Verbatim result: `REMAINING DEFECTS: 0 / 6`.

| audit finding | now |
|---|---|
| observe-only manifest accepted, undeclared fleet ACTIVE | refused at the admission seam (`fleet_not_authorized`), and by `main()` with nothing spawned |
| unauthorized target ACTIVATED | refused: coordinator/place disagreement and non-acquiring venues are `BindingRefused` |
| child past TTL with `RENEW CALLS 0` | child runs past TTL, **7 renewals**, lane accounted, released |
| failed run `FINAL STATE released` | `cleanup_pending`, `clean=False`, `release_raised` |

## What each requirement became

1. **Authorization and binding both precede every side effect.**
   `authorize_fleet_target()` is pure — it reads the manifest and resolves the
   target without opening a store. `bind_fleet_run()` then calls `check_binding`
   with the inventory/venue inputs and `verify_claim_handoff()` checks the
   child's claim against the binding. Only after all three succeed does
   `build_admission()` create the store.

2. **The runner exports the claim, it does not acquire the place.** The child
   environment gets `binding.as_env()` **plus**
   `TOLLGATE_FLEET_CLAIM_PLACE/OWNER/RUN_ID`, so `conftest`'s guard verifies the
   same place the runner reserved. `ClientIdentity.current` took a new optional
   environment mapping (`lib/labgrid_venue.py`) so the runner and the fixture
   derive the *same* owner string; without it the claim owner and the fixture
   owner would silently differ. The runner never calls acquire/release on a
   bench — `test_re_read_ownership_reads_the_coordinator_without_acquiring`
   fails if it ever does.

3. **Execution is supervised.** `supervised_tee_run(...)` is passed
   `authority/run_id/fence/ttl_s`, so the lease is renewed while the child runs
   and the child's process group is killed on renewal failure or authority loss.
   Log capture and exit-status propagation are unchanged; the non-fleet path
   still uses plain `tee_run`.

4. **Release is derived, never asserted.** `release_verdict(...)` decides from
   four facts — did the release raise, what did the read-only ownership re-read
   say, did the bench leave a `cleanup_pending` flag, was the place ever
   acquired — and its `clean` is passed to `admission.release(clean=...)`. There
   is no literal `clean=True` on this path. An unconfirmed release prints
   `RELEASE UNCONFIRMED` / `capacity remains consumed` and leaves the lane
   occupied. `release.json` records the verdict either way; if it cannot be
   written, that warns rather than masking the run.

## Tests

`tests/unit/test_runner_fleet_lifecycle.py` — 23 tests, RED first: **22 of 23
fail at base `ed687b2`** (`AttributeError: no attribute 're_read_ownership'` and
the refusal/marker assertions). The one that passes at base is the non-fleet
`tee_run` path, which must not change.

Real SQLite stores, real subprocesses, `re_read_ownership` and `LabgridBench`
stubbed at the boundary; no acquisition is ever performed, no coordinator or
hardware contacted.

Focused set: **349 passed**. Full `tests/unit`: 1831 passed, with 2 failures and
9 errors that are **identical at base** (`test_cudy_flash`, `test_read_only_guard`,
`test_hardware_lock` — environment/hardware gaps, not this change), plus
`test_story_evidence.py` which cannot even import (`No module named 'PIL'`).
`tests/unit/test_runner_lifecycle_gate.py` and the "gate still fails closed" test
in `test_supervised_run.py` were rewritten, deliberately and only where they
pinned the *blanket* refusal that this change replaces. Neither lost strength:
the gate file now asserts that each real gate refuses with exit 75 and spawns
nothing, and the `test_supervised_run.py` test asserts the non-fleet `tee_run`
path is untouched and that a fleet run no longer takes that path. No refusal
assertion was deleted or loosened.

Changed-code coverage **95.6%** (129/135 statements added or modified across
`scripts/run-profile.py` and `lib/labgrid_venue.py`); the 6 uncovered lines are
fallback returns and the `__main__` guard.

## What is still unwired or unproven

- **No real coordinator run has happened.** `LabgridBench.owner()`,
  `labgrid-client`, a live coordinator, and real DUTs are all stubbed here. The
  supervision loop, the claim handoff, and the release verdict are proven
  against real SQLite and real subprocesses, not against labgrid.
- **`re_read_ownership` has never parsed real `labgrid-client` output.** Its
  failure mode is safe (unreadable -> `None` -> `ownership_unreadable` ->
  `cleanup_pending`), but the success path is untested against the real CLI.
- **The fixture side is unchanged.** `tests/conftest.py` is byte-identical to
  base; the runner now exports the env it expects, but the two have never run
  together on hardware.
- **Concurrency was not exercised.** One lane, one run at a time; lane contention
  and fencing under parallel runs are untested.
- `pytest_cov` is absent from the repo venv, so coverage was measured with the
  `coverage` module directly; `tests/unit/test_story_evidence.py` cannot run
  here for lack of PIL.
