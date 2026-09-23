# Bench lanes: keeping PR code off the router

Status: implemented 2026-09-23 (PRTA-BENCH-SECURITY).

## The defect this closes

`.github/workflows/ci.yml` triggered on `pull_request` and carried two jobs,
`test-ui` and `test-physical`, on `runs-on: [self-hosted, tollgate-router]`.
Both were parked at `if: false` — *"Disabled until self-hosted runner is
configured"* — and the header comment claimed you had to uncomment the `runs-on`
lines to enable them. That comment was stale: the `runs-on` lines were already
live YAML, and `if: false` was the only gate.

That is a one-boolean distance from arbitrary code execution on the bench host
for **any contributor's PR**: the machine that flashes routers, holds the repo
secrets, and sits in the bench's L1/L2 neighbourhood. Worse, `test-physical`
force-enabled `TOLLGATE_ENABLE_WIFI_CLIENT_TESTS` and
`TOLLGATE_ENABLE_DATA_ALLOTMENT_TESTS`, which mutate router state and burn paid
traffic — a PR run would have corrupted the bench for the next agent. And
`test-physical` ran the *whole* Playwright config, destructive project included
(`tests/destructive/firmware-upgrade.spec.mjs`,
`reboot-recovery.spec.mjs`), i.e. firmware flash from a PR.

`publish` (`needs: [test-ui]`) could therefore never run: a job whose dependency
is skipped is itself skipped. A dead cascade, with no error to notice.

## The shape now

| workflow | triggers | runners | can a PR reach the bench? |
|---|---|---|---|
| `ci.yml` | `push: main`, `pull_request: main`, `workflow_dispatch` | `ubuntu-latest` only | no |
| `hw-smoke.yml` | `workflow_dispatch` + `schedule` (dormant) | `[self-hosted, tollgate-router]` | no — the workflow has no PR trigger |

`hw-smoke.yml` has three lanes:

| lane | needs | approval | mutating? |
|---|---|---|---|
| `readonly-surface` | — | none | no: no secrets, no credentials, no payment, no writes |
| `mutating-e2e` | `readonly-surface` | `bench-hardware` environment | yes; paid-traffic specs are **off** unless opted into per dispatch |
| `destructive-e2e` | `readonly-surface` | `bench-hardware` environment + `include_destructive` input | yes: firmware flash / reboot |

Every mutating lane runs `scripts/hw-readonly-check.sh` **first**: the read-only
gate is a precondition, not a parallel nicety. `concurrency: hw-bench` serialises
all bench work and never cancels (`cancel-in-progress: false`) — a half-finished
mutating lane leaves the router transitional.

## Enabling hardware CI (operator checklist)

1. Register a self-hosted runner on the bench labelled `tollgate-router`.
   Today `gh api repos/felixfelix-bot/physical-router-test-automation/actions/runners`
   returns `{"total_count":0}` (admin-readable, so authoritative), and upstream
   reads 403 ⇒ *unseen*, not *absent*.
2. Create the `bench-hardware` environment **with required reviewers**. The
   mutating lanes declare `environment: bench-hardware`, so without reviewers
   GitHub auto-creates the environment unprotected — the approval gate is the
   operator's half of the contract.
3. Set repo variable `HW_BENCH_SCHEDULE=true` to arm the weekly read-only run
   (and `HW_BENCH_HOST` if the bench is not at 192.168.1.1). Until then the cron
   self-skips, so there are no queued-forever runs.
4. `make hw-readonly` is the local equivalent of the read-only lane and needs no
   lock, no secrets and no runner.

There is no `if: false` anywhere in `.github/workflows/`; enablement is a
dispatch input, a repo variable, or an environment approval instead. That is
deliberate — a disabled job behind a boolean is an invitation to flip it.

## The guard

`scripts/ci/check-workflow-hw-isolation.sh` runs in the `ci.yml` `lint` job on
every PR and fails on:

- **R1** a `pull_request`/`pull_request_target`-triggered workflow that
  references `self-hosted` or a bench-mutating env flag;
- **R2** `hw-smoke.yml` missing, PR-reachable, or carrying a trigger outside
  `{workflow_dispatch, schedule}`;
- **R3** any `if: false` job guard in the tree;
- **R4** a job referencing the bench-mutating env flags without `environment:`;
- **R5** a job in the hardware workflow using `secrets.` without `environment:`.

It scans effective YAML only (comments stripped), so this documentation can name
the anti-patterns it bans.

`scripts/ci/test-check-workflow-hw-isolation.sh` injects one regression at a time
into a copy of the real tree and asserts the guard fails with the right rule —
9 cases, all green. A guard never seen red is decoration, not evidence.

## Read-only vs mutating locally

`scripts/hw-readonly-check.sh` gates, in order:

- **G0** refuse to run at all if `TOLLGATE_ENABLE_WIFI_CLIENT_TESTS` /
  `TOLLGATE_ENABLE_DATA_ALLOTMENT_TESTS` is enabled — a read-only lane that
  silently carries mutating flags is how the bench gets corrupted;
- **G1** `:2121/` answers 200 **and** is in FULL mode (`kind: 10021` with
  `price_per_step`). Degraded mode is a FAIL, never adapted to;
- **G2** `:2121/balance` is parseable; unreadable ⇒ treated as NOT idle (fail
  closed);
- **G3** `scripts/tollgate-port-sweep.sh` (when present) must pass; absence is a
  loud PARTIAL, not a silent pass.

Exit codes: `0` pass, `1` gate failed, `2` refused to run. The last stdout line
is a JSON summary for artifacts.

## Known gaps (not fixed here)

- `publish` is now `needs: [lint]` and gated behind the `PUBLISH_TEST_REPORTS`
  repo variable: the report lane is not yet runnable from a hosted runner.
  `scripts/run-tests.sh` → `scripts/run-profile.sh` requires a Python venv at
  `$HOME/.tollgate-test-venv` that no CI step creates, and `TOLLGATE_LUCI_URL`
  points at a LAN-only bench. It also never ran before, so nothing regressed.
- `package.json`'s `publish-report` script points at `scripts/publish-report.sh`,
  which does not exist in the tree.
- `tests/helpers/inventory.mjs getRouter()` still falls back to a stale
  `192.168.13.112` when neither `TOLLGATE_LUCI_URL` nor `config/routers.json` is
  set; the lanes pin `TOLLGATE_ROUTER_HOST` explicitly to avoid it.
