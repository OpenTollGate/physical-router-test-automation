# Fleet capacity: read-only observation and the operator CLI

Status: **observation only**. Nothing in this slice acquires a place, powers a
device, flashes hardware, starts a VM or enables parallel execution. The
`NOT_QUALIFIED` admission status below is the point of the slice, not a
disclaimer bolted onto it.

Source of truth for the rules: `reports/prta-fleet-capacity-audit.md`
(gates 1, 4, 5, 7). Slice history:

| slice | module | what it decides |
|---|---|---|
| 1 | `lib/fleet_capacity.py` | what a *given* piece of evidence supports — pure, no I/O |
| 2 | `lib/fleet_authority.py` | one host's serialized atomical admission ledger |
| 3 | `lib/labgrid_venue.py` (ownership) | per-run coordinator identity, conditional release |
| 4 (this) | `lib/fleet_observe.py` + `scripts/fleet-capacity.py` | read-only adapters that *produce* the evidence slice 1 decides on |

Slice 1 shipped `run_probes` with an injected callable and no adapter in the
tree. This is that adapter, and it exists so an operator can answer "what could
this authorized fleet do right now, and if nothing, why not" without the answer
being a guess.

## What it reports, and why the numbers are separate

Four counts, never added together:

1. **reachable fleets** — the coordinator answered. Certifies the control plane
   and nothing else. A reachable coordinator with no exporter is capacity zero.
2. **idle healthy physical routers** — requires owner-authorized fresh health
   telemetry: identity match, health, no active paid session, client vantage.
   Without that telemetry the state is `exported_available` at **capacity 0**.
   Exported is not idle.
3. **idle existing QEMU guests** — requires process identity (pid **and** start
   ticks, so a recycled pid is refused), health, topology integrity, a free
   authoritative lease and client reach. This adapter can only identify a guest
   from a pidfile and `/proc`, so it never proves readiness and reports
   `unknown` rather than a lane.
4. **additional VM slots** — host admission, and the only number this slice
   cannot honestly produce. See `NOT_QUALIFIED` below.

Stale, unreachable and unknown are never free. Every refusal carries a reason
code from `lib.fleet_capacity.Reason` and the CLI prints it inline.

## NOT_QUALIFIED (read this before trusting a slot count)

`ADMISSION_STATUS = "NOT_QUALIFIED"` is printed with every report, in text and
JSON, and `new_vm_slots` is labelled `raw`.

The reason is structural, and it is **two** reasons now that slice 2 landed —
neither is "we forgot to build accounting":

1. `cpu_committed` cannot be measured from `/proc`. A running QEMU guest's vCPU
   claim is not a host-level counter, so any slot arithmetic that needs it is a
   guess in the dangerous direction (a busy host would look free).
2. This CLI opens the authority store **read-only** (`file:...?mode=ro`).
   `FleetAuthority.__init__` creates/migrates the store, i.e. it is a writer, and
   an observation tool must never be a writer on a live bench. So the CLI
   deliberately does not call `host_headroom()`.

Admission therefore lives in the runner (`lib/run_admission.py`), which holds a
real owned, fenced lease against the host pool before it spawns anything. This
adapter:

* measures real host numbers (CPU count, `/proc/meminfo`, `statvfs`,
  `/dev/kvm`) — **not** a laptop's guess about a remote box;
* marks `cpu_committed` an **unmeasured dimension**, which makes
  `new_vm_slots` answer `unknown`/0 instead of inventing headroom;
* reads outstanding reservations from the host's own authority store, so
  started allocations are not double-counted;
* refuses any `host_controller` endpoint that is not this machine — reading
  `/proc` while claiming to describe another host would be a fabrication.

A raw headroom estimate is still printed, because an operator diagnosing a full
host needs the numbers. It is never presented as allocatable.

## Usage

```bash
# everything the manifest authorizes; nothing else is contacted
python3 scripts/fleet-capacity.py --manifest ~/fleets.yaml

# machine-readable, for a dashboard or a later runner integration
python3 scripts/fleet-capacity.py --manifest ~/fleets.yaml --json

# declare the existing QEMU lanes by pidfile (never started here)
python3 scripts/fleet-capacity.py --manifest ~/fleets.yaml \
    --guest lane-a=$HOME/tollgate-virtual-lab/run/tollgate.pid \
    --guest lane-b=$HOME/tollgate-virtual-lab/run/debian-client.pid

# outstanding reservations from the host authority (read-only, mode=ro)
python3 scripts/fleet-capacity.py --manifest ~/fleets.yaml \
    --authority /var/lib/prta/fleet-authority.sqlite3

# save the evidence, then watch it decay without probing again
python3 scripts/fleet-capacity.py --manifest ~/fleets.yaml --save-evidence /tmp/ev.json
python3 scripts/fleet-capacity.py --manifest ~/fleets.yaml --from-snapshot /tmp/ev.json
```

Flags that change the *answer*, not just the formatting:

| flag | meaning |
|---|---|
| `--fleet ID` | limit to named fleets (repeatable); an unconfigured id raises, never falls back |
| `--timeout S` | per-probe subprocess timeout (default 10) |
| `--ttl S` | evidence freshness budget (default 60) — how fast a snapshot becomes `stale` |
| `--epoch N` | authority epoch; read from the authority store when one is configured, otherwise required |
| `--ram-reserve-mib N` | operator-declared safety reserve, subtracted from headroom |
| `--demand-*` | the declared vector of ONE lane, printed next to the answer |
| `--from-snapshot PATH` | re-decide saved evidence; **no coordinator or host is contacted** |

Exit codes: `0` report produced (including "no capacity"), `2` manifest refused,
`3` evidence refused (bad snapshot schema, epoch mismatch, unmeasurable host).

Example against a local labgrid coordinator (real output, 2026-10-30):

```
generated: 2026-10-30T20:15:11+00:00  (evidence age 0s, ttl 60s)
manifest:  .../manifest.yaml
epoch:     1  admission: NOT_QUALIFIED

fleet                 coord   exporters  routers  guests  vm-slots(raw)
--------------------  ------  ---------  -------  ------  -------------
local-lab             up              1        0       0              0

totals: 1/1 fleets reachable, 0 idle physical router(s), 0 idle existing guest(s), 1 exported-but-not-proven-idle
new VM slots: 0 raw / NOT_QUALIFIED — not allocatable (see below)

[local-lab]
  coordinator: local-lab/coordinator:local-lab: coordinator_reachable capacity=0 (control_plane_only)
  host admission: unknown slots=0 (unknown_dimension)
  host metrics (this machine, measured now): cpu=8 load1=20.06 ram_free=1170MiB/7427MiB swap_used=7420MiB disk_free=1120MiB inodes_free=13893675 kvm=True arch=x86_64
  excluded (2):
    lane-a: exported_available (no_telemetry) — exported and available, but no owner-authorized health telemetry — exported is not idle
    local-qemu-a: unknown (no_telemetry) — no guest observation
outstanding reservations: none readable (no host authority store configured ...)
```

## Read-only by construction

`LabgridReadOnlyClient` can issue exactly `version`, `resources`, `places` and
`show`. The allowlist is enforced above the process boundary and the command
line shape is fixed (no free-form argument passthrough), so `acquire`,
`release`, `power`, `flashscript`, `create` and `delete` raise
`ObservationRefused` **before** any subprocess exists. A unit test asserts the
refusal for each of them and that the runner was never called.

The allowlist pins the *subcommand*; the two values that precede it are pinned
too. `-x <coordinator>` and `-p <place>` are values, and labgrid-client's own
argparse would re-tokenize a leading `-` — so a coordinator or place id starting
with `-` is refused here rather than offered to it. Without that check a manifest
value of `--help` reached labgrid as a flag, and "the command line shape is
fixed" would only have meant "position 5 is fixed".

Place discovery uses the manifest's `allowed_places`, never the coordinator's
place list: reading the list would turn observation into discovery, and
discovery is not authorization. `resources` is read only as narrative evidence
that an exporter is publishing.

## An unreadable reservation is a refusal, not a zero

Reading the host authority is a `SELECT` over `file:...?mode=ro`, built with
`Path.as_uri()` so a store path containing `?`, `#` or `%` is escaped. Text
interpolation (`f"file:{path}?mode=ro"`) truncates the URI at the first `?`:
measured, `.../a?b#c%d.sqlite3` opened `.../a`, so a real store was reported
"not found" and a different file could have been read in its place.

If an *occupying* allocation's `demand` column cannot be parsed, the read does
not silently drop it. Dropping it would price a live claim at zero — the
dangerous direction — so instead:

* `ReservationRead.available` is `False` and `ReservationRead.unsized` names the
  run ids;
* the rows that *could* be priced are still returned (losing them would be a
  second silent loss of evidence);
* `collect()` records a `Refusal` with reason `reservations_unsized`;
* `new_vm_slots(..., reservations_incomplete=True)` returns `unknown`/0 with
  that reason code instead of a slot count derived from a partial ledger;
* the CLI prints `outstanding reservations: INCOMPLETE — ... headroom is
  unknown, not free` and the JSON report carries `reservations.complete: false`.

Evidence snapshots are schema version 2: the version bump is what makes an
old snapshot refusable rather than reinterpreted under the new field set.

## Secret safety

Everything a probe or a manifest can emit passes through
`lib.fleet_capacity.redact` — PEM blocks, `nsec`, GitHub/OpenAI/Slack-shaped
tokens, Cashu tokens, `password: ...`-shaped pairs and 64-hex digests. Evidence
snapshots are written through it too, so `--save-evidence` cannot become a
credential drop. Manifests carry `(source, name)` credential *references*;
there is no code path that reads a credential value.

## Example manifest

`config/fleets.example.yaml` is the schema, with placeholders. Point the CLI at
a local copy. A working local-lab shape (this is what the tests and the run
above used):

```yaml
schema_version: 1
fleets:
  - id: local-lab
    owner: prta-smoke
    coordinator:      {address: 127.0.0.1:20511}
    host_controller:  {endpoint: 127.0.0.1:8099}   # must be THIS host to be measured
    allowed_places:
      - lane-a
      - {id: local-qemu-a, kind: guest}
    permitted_actions: [observe]
    capacity_limits: {max_concurrent_runs: 2, max_idle_guests: 1, max_new_vm_slots: 0}
    shared_conflict_ids: []
```

## What is deliberately not here

* No acquisition, no lease renewal, no release. `--from-snapshot` cannot change
  anything; a live run cannot either.
* No VM start, no image build, no overlay creation, no network setup.
* No SSH, no power control, no flashing, no subnet scan, no home-directory
  inventory sweep — "no capacity was found" must never be the result of a
  process that went looking.
* No router health probe. Owner-authorized telemetry is an *input*; inventing
  it here would be exactly the "exported means idle" bug the audit names.
* No claim of automatic parallel execution. Wiring observations into
  `scripts/run-profile.py` with owned atomic reservations is FLEET-CAP-5 and is
  not done.

## Tests

```bash
# pure + fake-runner unit tests (no coordinator, no hardware)
python3 -m pytest tests/unit/test_fleet_observe.py -q

# real pinned labgrid coordinator + exporter (required: absence is a failure)
python3 -m pytest tests/integration/test_fleet_observe_coordinator_real.py -q
```

The integration module starts the actual `labgrid-coordinator` and
`labgrid-exporter` binaries on a free loopback port, creates real places, and
asserts: a free place is exported-not-idle; a place held by another identity is
refused with `place_acquired`; an unknown place is missing rather than free;
with the exporter stopped the coordinator stays up and capacity disappears; a
read issues no mutating subcommand and leaves the ownership table unchanged;
reservations round-trip from a real authority store without being touched; and
a saved snapshot re-decides offline, then decays to `stale` past its TTL and to
`epoch_mismatch` under a new authority epoch.

It proves nothing about physical DUTs: no router and no QEMU guest is involved.
Physical fleets stay opt-in and unqualified until their owners authorize a live
identity/idle/vantage/lease test.
