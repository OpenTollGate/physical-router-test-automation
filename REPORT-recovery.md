# Slice 3 recovery — t_2d4a7a54

## Verdict: NOT QUALIFIED — published savepoint, not benchmark success

Bounded ship/verify recovery on 2026-10-30. Initial worktree was clean. The worker had already committed AND published `f45ff0140673b357292078b73596ca0d4f052ffb` on `fleet-cap-3-benchmark`; `git ls-remote fork refs/heads/fleet-cap-3-benchmark` matched local HEAD. This includes the earlier identity/coordinator milestone `f9ec46251b05fcc8343fe7552e9fbff752746acf`. No implementation changes were needed or made in recovery.

Branch handle: https://github.com/felixfelix-bot/physical-router-test-automation/tree/fleet-cap-3-benchmark

No slice-3 PR was found by head-branch queries in either the fork or upstream (including closed PRs). Existing authority PR is https://github.com/OpenTollGate/physical-router-test-automation/pull/332, head `c883b090d32c82682a34973a9db7faac69ab5861`, branch `fleet-cap-2-atomic-reservation`: it does NOT contain this slice-3 tip. No competing PR created; no PR changed, merge, force push, or card transition performed.

## Recovery validation (against f45ff0140673b357292078b73596ca0d4f052ffb)

Interpreter: `/home/c08r4d0r/.tollgate-test-venv/bin/python`, Python 3.12.3, pytest 9.1.1, labgrid plugin 26.0.

| Gate | Actual result | Evidence scope |
|---|---|---|
| A + D | `45 passed in 0.32s`, exit 0 | Ownership unit doubles (27), benchmark fixture/admission + lane-planner contracts (18); not running guests |
| C | `2 failed, 3 passed in 0.88s`, exit 1 | Aggregate authority tests with `--runxfail`: failure preserved, not expected-failure green |
| B | Historical committed report says nine real coordinator/exporter cases pass | Not rerun in bounded recovery; raw prior logs unavailable, so not independently reverified here |
| E | Serial admission dry-run REFUSED, exit 3 | Live raw host metrics only; no QEMU launch or throughput measurement |
| Shell syntax | exit 0 | `bash -n scripts/fleet-cap-3-qualify.sh` only |

Executed focused commands:

```sh
/home/c08r4d0r/.tollgate-test-venv/bin/python -m pytest tests/unit/test_labgrid_venue_ownership.py tests/unit/test_fleet_bench_gate.py -q -p no:cacheprovider
/home/c08r4d0r/.tollgate-test-venv/bin/python -m pytest tests/unit/test_fleet_authority_aggregate.py -q -p no:cacheprovider --runxfail
/home/c08r4d0r/.tollgate-test-venv/bin/python scripts/fleet-bench.py --config serial --dry-run --out results/recovery-fleet-cap-3
bash -n scripts/fleet-cap-3-qualify.sh
```

Preserved failing assertions:

```text
AssertionError: three 256 MiB lanes were admitted on a 512 MiB host — the authority enforces per-lane budgets only, so nothing stops aggregate overcommit
assert True is False
AssertionError: no host-level headroom accessor: a runner cannot know the host is full
assert False
2 failed, 3 passed in 0.88s
```

Live admission snapshot: 8 cores, load1 4.46, MemAvailable 1939 MiB, swap used 63.2%, home free 1583 MiB, KVM available; foreign QEMU PIDs 485984, 910845, 910876. Required serial RAM 1408 MiB and disk 1024 MiB. Refusal reasons: swap exceeds 25% and foreign QEMU processes exist. These are raw host observations, not shared-host reservation or fleet qualification. Dry-run JSON is local ignored output at `results/recovery-fleet-cap-3/admission-serial.json`; key values are preserved here.

## Qualification evidence limits

`docs/FLEET-CAP-3-QUALIFICATION.md` records an earlier full qualification: A/B/D PASS, C NOT QUALIFIED, E REFUSED, overall exit 1. Its referenced `results/fleet-cap-3/{qualification.txt,toolchain.json,gate-A.log,gate-B.log,gate-C.log,gate-D.log,gate-E.log,admission-serial.json}` are all absent from this worktree during recovery. No gate logs or qualification.txt were found elsewhere under this worktree's results directory. The historical real coordinator result is therefore committed narrative evidence, not newly verified raw execution evidence.

The full qualifier was deliberately NOT rerun: gate E invokes the real benchmark without `--dry-run`, which could launch guests if admission changes. Code inspection confirms gate C uses `--runxfail`, sets `fail=1` on failure, and final failure takes priority over refusal, returning 1 with `RESULT: NOT QUALIFIED`. Recovery directly reproduced gate C's nonzero failure without risking a guest launch. No benchmark success is claimed, and no missing evidence was fabricated.

## Outstanding gaps / handoff

- Aggregate shared-host admission is absent; per-lane budgets do not prevent cross-lane overcommit. This is the qualification blocker, not a test to weaken.
- No serial/parallel/oversubscribed throughput was measured. QEMU lane substrate is built but actual QEMU execution remains unverified. Physical hardware remains unqualified.
- Live observation adapters, operator-facing capacity CLI, and run-profile scheduler wiring remain outstanding in this branch. Separate ready task `t_3ad7fda2` owns live adapters; none of its files were edited.
- Real coordinator gate B needs accessible raw evidence or a future bounded rerun before presenting it as freshly independently verified.
- Worker stopped at iteration limit (160); an OOM wrapper label is not evidence of an actual OOM.

Recovery adds this report only. The report commit is a documentation savepoint atop the already-published implementation milestone; exact post-push local/remote tip is returned separately to avoid a self-referential commit hash.
