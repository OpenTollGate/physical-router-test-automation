# PROGRESS — t_2d4a7a54 (FLEET-CAP-3: measure QEMU serial-vs-parallel throughput)

Worktree: `/home/c08r4d0r/worktrees/t_2d4a7a54` — own worktree, branch `fleet-cap-3-benchmark`
forked from the published slice-2 commit `c883b090d32c82682a34973a9db7faac69ab5861`
(PR332 head; reused as-is, no redesign).
Source of truth for acceptance: `/home/c08r4d0r/reports/prta-fleet-capacity-audit.md` gates 7-9.

> The inherited `PROGRESS.md` in this worktree belongs to unrelated task `t_a4aa351d`
> (bench-lock consolidation). This file is this task's log.

## Evidence tags used throughout

* **REAL-COORD** — observed against a running `labgrid-coordinator` (the wheel installed on
  this host), not a double.
* **DOUBLE** — asserted against `tests/unit` fakes only.
* **HOST-METRIC** — read from this host now.
* **FIXTURE** — a value a test supplied; not detection.
* **UNPROVEN** — not measured; recorded as a gap, never as a pass.

## Pinned labgrid (REAL-COORD)

    labgrid 26.0.1  /home/c08r4d0r/.hermes/hermes-agent/venv        (python 3.11.15)
    labgrid 26.0    /home/c08r4d0r/.tollgate-test-venv              (python 3.12.3)

Both ship `labgrid-coordinator`, `labgrid-exporter`, `labgrid-client`. The repository pins
neither: `requirements.txt` / `requirements-dev.txt` contain no `labgrid` entry, so "pinned"
here means "the version actually installed on this host, recorded with its path and
`labgrid.__version__`" (the integration harness prints both). Recording that absence is part
of the result: CI has no labgrid at all, which is why gate 7 must FAIL, not skip.

## Milestone 0 — native ownership semantics, measured (REAL-COORD)

    labgrid-coordinator -l 127.0.0.1:20499
    labgrid-exporter -c 127.0.0.1:20499 -n probe-exporter --hostname probe-exp exporter.yaml
    labgrid-client -x 127.0.0.1:20499 -p lane-a create
    labgrid-client -x 127.0.0.1:20499 -p lane-a add-match probe-exporter/probe-nic/NetworkService

| Step | Observed |
|---|---|
| `resources` | `probe-exporter/probe-nic/NetworkService` |
| `places` | `lane-a` |
| `show` (free) | `acquired: None` |
| acquire as `h/prta-a` | rc=0, `acquired place lane-a` |
| acquire as `h/prta-b` | **rc=1**, `Place lane-a is already acquired by h/prta-a` |
| `release-from h/prta-b` (non-owner) | **rc=0**, prints `h/prta-b has released place lane-a`, but `acquired:` is still `h/prta-a` |
| `release-from h/prta-a` (owner) | rc=0, place free |

The `release-from` row is the load-bearing fact the wrapper exists for: **success output does
not prove a release happened.** `lib/labgrid_venue.release()` must re-read ownership; that is
now asserted against the real coordinator, not a fake.

`labgrid-client --help` (REAL-COORD) also confirms the native reservation surface exists:
`reserve`, `cancel-reservation`, `reservations`, `wait` — the wrapper does not use it, so its
ownership semantics had to be measured before anyone depends on it (see the integration test).

## Host capacity at start (HOST-METRIC, 2026-10-30 20:10 CET)

    8 vCPU, load average 22.4  (unrelated concurrent sessions on this host)
    MemTotal 7.6 GB, MemAvailable 1.7 GB, SwapTotal 7.6 GB, SwapFree 1.1 GB (~86% used)
    / 247 GB, 211 GB used, 25 GB free (90%)
    /dev/kvm present, kvm-group accessible; two unrelated meshlink-lab QEMU guests running

**Blocker for the headline measurement (gate 8).** The host is already oversubscribed by work
this card does not own. Per the operator steer, the QEMU benchmark is *not* launched at these
numbers and the workload is *not* shrunk to manufacture a result. CPU pinning is not exclusive
CPU reservation, so pinning two guests here would only change which processes suffer.
Recorded prerequisite for resuming: MemAvailable ≥ 2×lane peak + 1 GB reserve, swap use < 25%,
load average below core count, and no foreign QEMU leases — measured immediately before the
run by the harness's own admission gate, which refuses (exit 3) rather than proceeding.

## Current state / limits

* Coordinator qualification (gate 7) is low-resource and is being finished now.
* QEMU throughput measurement (gate 8): harness + admission gate published; **numbers pending
  the resource blocker above**. A measured "blocked" is reported, never an invented speedup.
* `lib/fleet_scheduler.py` is imported only by its unit tests — `scripts/run-profile.py` does
  not reference it. Scheduler measurements are therefore library-level, and "parallel runner"
  is not yet a product feature.
* Slice 1 is classification only: `lib/fleet_capacity.run_probes` requires an injected
  callable and no module in the tree supplies a real coordinator/exporter/VM-host adapter.
