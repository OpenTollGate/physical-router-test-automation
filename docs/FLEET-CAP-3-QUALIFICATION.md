# FLEET-CAP-3 qualification record — t_2d4a7a54

Date: 2026-10-30. Base: `f9ec462` on branch `fleet-cap-3-benchmark`, forked from the
published slice-2 commit `c883b090d32c82682a34973a9db7faac69ab5861` (PR332 head).
Source of truth for the gates: `/home/c08r4d0r/reports/prta-fleet-capacity-audit.md`
gates 7-9.

**Verdict: NOT QUALIFIED.** Two capabilities the audit requires are missing, and the
headline measurement is refused by its own admission gate because this host has no
measured safe headroom. No throughput number is claimed.

## The one command

    TOLLGATE_QUALIFY_PYTHON=/home/c08r4d0r/.tollgate-test-venv/bin/python \
      ./scripts/fleet-cap-3-qualify.sh

Exit codes are the contract: `0` qualified, `1` not qualified, `3` benchmark refused
for lack of safe host headroom. Latest run (`results/fleet-cap-3/qualification.txt`):

    == FLEET-CAP-3 qualification 2026-10-30T20:44:58+01:00
       head    f9ec46251b05fcc8343fe7552e9fbff752746acf
       labgrid 26.0 /…/.tollgate-test-venv/lib/python3.12/site-packages/labgrid/__init__.py
    gate A     PASS  per-run identity + owned release (unit, doubles)
    gate B     PASS  real pinned coordinator (no doubles)
    gate C     NOT QUALIFIED  aggregate host admission missing (owed) — see gate-C.log
    gate D     PASS  benchmark admission gate + lane isolation contract
    gate E     REFUSED  serial — MemAvailable 1190 MiB < required 1408 MiB
                                    (1 lane(s) x 384 + 1024 reserve);
                        swap already 61.7% used (limit 25%);
                        load1 17.46 > 1.0 x 8 cores — the box is busy;
                        foreign QEMU processes already running: [485984, 910845, 910876]
    RESULT: NOT QUALIFIED (see gates above)
    NOTE: benchmark additionally REFUSED for lack of safe host headroom

## What is REAL vs what is not

| Gate | Evidence class | Command |
|---|---|---|
| A | unit, against a fault-injecting **double** | `pytest tests/unit/test_labgrid_venue_ownership.py -q` |
| B | **real** pinned coordinator + exporter processes | `pytest tests/integration/test_labgrid_coordinator_real.py -q` |
| C | unit; failing cases are **xfail** documentation, run with `--runxfail` for the verdict | `pytest tests/unit/test_fleet_authority_aggregate.py -q --runxfail` |
| D | unit, **fixture** host metrics + real lane-planner checks | `pytest tests/unit/test_fleet_bench_gate.py -q` |
| E | not run — refused | `python3 scripts/fleet-bench.py --config serial` (exit 3) |

Gate B is the only place where a real coordinator was involved, and it is the only
gate that can move a claim from "modelled" to "measured". Its nine cases pass against
labgrid **26.0** here (26.0.1 in the other venv on this host; both were exercised
during recon). Measured facts that changed our assumptions:

1. **`release-from` by a NON-owner exits 0 and prints "has released place" while
   changing nothing.** This is the load-bearing fact for slice 2's conditional
   release: the exit code cannot be trusted, only the ownership re-read can.
2. **A lease outlives its client.** A crashed client does not free the place, so
   cleanup cannot be inferred from disconnect.
3. **Native reservations exist and are owner-scoped**: `reserve KEY=VALUE` allocates a
   matching place to the reserver; another identity is refused with "was not reserved
   for"; `cancel-reservation <token>` frees it. Our wrapper does not use them yet.
4. **A restart keeps places but DROPS ownership** — and persistence is a 15-second
   poll, so a place created just before SIGTERM may not be on disk at all. This is the
   measured justification for invalidating the authority epoch on restart.

Missing labgrid is FAILED, not skipped: the module carries `required`, and the
suite-wide gate in `tests/conftest.py` turns a skipped required test into
"NOT VERIFIED". Verified by pointing `TOLLGATE_LABGRID_CLIENT` at a non-existent
binary: the gate errors out instead of passing green.

## Fixes delivered in this slice

**1. Owner-identity collision (RED-first, verified against the old code).**
`ClientIdentity.for_run` sanitized and truncated the run id, neither step injective:
`for_run("run/1")` and `for_run("run-1")` produced the same owner, and two ids
differing only past character 60 collapsed too. Two runs on one owner re-opens
exactly the race slice 2 fixed. The identity now appends a digest of the **original**
run id. Evidence: the three collision tests fail on `c883b09` (checked by running
them against an extracted copy of the pre-fix `lib/`) and pass on the fix.

**2. Aggregate host admission is MISSING (slice-2 gap, found here).**
`FleetAuthority` budgets strictly **per lane**. Three lanes each declaring 256 MiB
are all admitted on a hypothetical 512 MiB host — 768 MiB of admitted demand. There
is no host-pool field, argument or row, so this is an **unsupported configuration**,
not a violated configured budget: nothing was ignored, the capability does not exist.
Consequence for this card: a runner that admits lanes one at a time can overcommit a
host, and "no oversubscription beyond the admitted budget" holds per lane while
failing across lanes. Raw failing evidence (`--runxfail`, exit 1):

    AssertionError: three 256 MiB lanes were admitted on a 512 MiB host — the
    authority enforces per-lane budgets only, so nothing stops aggregate overcommit
    assert True is False

    AssertionError: no host-level headroom accessor: a runner cannot know the host
    is full
    assert False

**3. Benchmark substrate + admission gate (built, not yet exercised).**
`lib/qemu_lane.py` (one guest per lane: unique overlay, NIC, console socket, log,
event log; serial-console command driver with unique completion markers) and
`scripts/fleet-bench.py` (three configurations against a real `FleetAuthority`,
overlap proven from per-job timestamps, REFUSED on missing KVM / short RAM / used
swap / high load / foreign QEMU). Its gate and lane-isolation contract are tested
with fixture metrics; **no guest was started on this host** — every configuration
exits 3 here.

## Remaining implementation gaps (owed, not optional)

These are the manager's point made concrete: slice 1 is classification only, and a
standalone library is not a user-visible feature.

1. **No real observation producers.** `lib/fleet_capacity.run_probes(manifest, probe)`
   calls an **injected** callable per authorized endpoint
   (`observation_plan()` yields `ProbeTarget(fleet_id, role, endpoint)` for role in
   `{"coordinator", "host_controller"}`). Nothing in the tree supplies that callable.
   The observation types exist and are constructible — `coordinator_observation`,
   `exporter_observation`, `router_observation`, `guest_observation`, plus
   `HostObservation`/`new_vm_slots` for slots — but every value is the caller's
   measurement. **No adapter turns a live coordinator/exporter/VM host into these.**
   Minimal producer set, in order of value:
   * `lib/observation_probes.py::LabgridProbe` — run `labgrid-client -x <addr> resources`
     / `places` / `show -p <place>` and emit `coordinator_observation(reachable=…)`,
     `exporter_observation(exporter_connected=…, acquired=…)`,
     `router_observation(...)`; timestamps from `time.time()`, `ttl_s` from the
     manifest.
   * VM-host probe — `lib/qemu_lane.py` already exposes the primitives
     (pid + `/proc/<pid>/status` `VmHWM`, overlay presence, console reachability) that
     `guest_observation(pid=, pid_start_ticks=, bridge_present=, health_ok=, …)` wants.
2. **No operator-facing command.** No CLI exists for `lib/fleet_capacity`; there is no
   `fleet_capacity.py` entry point and no argparse anywhere in it. Needed: one command
   that takes `--manifest`, builds observations through (1), calls
   `router_capacity`/`guest_capacity`/`new_vm_slots` then `summarize`, and prints per
   fleet: available resources, healthy idle routers, idle QEMU guests, additional VM
   slots — each with `observed_at` and reason code. Roughly 150 lines in
   `scripts/fleet-capacity.py`.
3. **No runner wiring.** `lib/fleet_scheduler.FleetScheduler.allocate(ScheduleRequest)`
   is a context manager yielding a `Lease` whose `.run(poll_s=...)` supervises the
   child; `ScheduleRequest(fleet_id, lane_id, resource_id, run_id, demand, domains,
   ttl_s, command, cwd, env)`. `scripts/run-profile.py` does not import it — insertion
   point per the audit is immediately before `tee_run` in `main()`, after profile/env
   resolution. **Nothing runs in parallel in production today.**
4. **Aggregate host pool** (fix 2 above): needs a host resource with a total budget and
   cross-lane reservation, plus a `host_headroom()` accessor, before any multi-lane
   default is safe.

## What is measured on QEMU vs what remains unproven

* **QEMU:** nothing yet. The three-configuration measurement is blocked by measured
  host capacity and by gap 4; when it runs it will be QEMU-only.
* **Physical hardware:** unproven and out of scope for this card. Audit gate 9 stands:
  owner authorisation per device, proven identity/idle/vantage/power/RF constraints,
  bypass writers (#287/#289) disabled or integrated, and coexistence tests. **Virtual
  success does not qualify hardware behaviour.**

## Reproducing the refusal

    python3 scripts/fleet-bench.py --config serial --dry-run

prints the live `host_metrics()` (cores, load, MemAvailable, swap %, home free, KVM,
foreign QEMU pids) and exits 3 with the reason string. Shrinking `--mem-mib` to force
a pass is not a tuning knob in this harness: `RAM_PER_LANE_MIB` and
`RAM_RESERVE_MIB` are module constants with the measured justification next to them.
