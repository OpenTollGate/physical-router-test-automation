# Local-lab soak — 2026-10-13

Venue: ai-legion local virtual lab (QEMU OpenWrt 24.10.1 + Debian client,
`tg-poc-br` 10.99.99.0/24), base = `main` at merge `53083c6` (PR #231
included). Purpose: exercise the 2026-10 PR wave (#190–#231) on a real
OpenWrt target after the org Actions freeze left every merge CI-untested.

## Results

| Target | Result | Meaning |
|---|---|---|
| `TestSpaPortal2051` (PR #231, fix F7) | **PASS 4/4** | The `:2051` SPA (`/etc/tollgate/tollgate-captive-portal-site`, uhttpd) has real passing coverage on live hardware — entrypoints exist, `splash.html` + `balance.html` served 200, `listen_http` binds the port. The "zero passing coverage while dashboards read 0 failed" gap is closed. |
| `tests/api/test_health.py` (required-marked baseline) | **PASS 4/4** | Backend healthy on merged main. |
| `test_visual_time_metering_expiry` | **PASS** | Full payment rail: CDK fakewallet mint → `pay_direct kind=1022` → NDS auth → expiry → internet cutoff. Also exercised PR #200's assert-first wifi connect: the VM ships IPv6-off by itself and `assert_ipv6_disabled_on_lan()` held. |
| `tests/api/test_deployed_contracts.py` (PR #196) | 4 SKIP, each with its designed reason | The VM's persistent overlay runs a TollGate build that predates the contracts (`entry_ui` schema < v0.0.10, text/plain ad on old firmware, no opkg comparator path, destructive test opt-in). Skip-is-signal working as designed. |
| `tests/api/test_train_topology_scenarios.py` (PR #201) | 3 SKIP | Exact drift signal: "stop-ship train not deployed on this DUT … this skip is the drift signal." |

## Failures

`test_visual_happy_path` — **environmental, not a regression**: the
Debian client VM has no `playwright` module (no pip on the image, 960 MB
RAM). Playwright + Chromium is cloud-snapshot bake payload only
(`bake-snapshot.py`); the local lab has never shipped it. The payment e2e
this test would record already passed via the metering test. To make the
local venue happy-path-capable, a provision step would need
`python3-pip` + `pip install playwright` + `playwright install chromium
--with-deps` on the Debian overlay — not done (RAM/disk cost vs. one
recorded video).

## Operational notes

- **`start-poc` serial-fallback timeout is recoverable**: first
  invocation hit "Serial console provisioning failed/TIMEOUT"; re-run
  detected the already-provisioned overlay, SSH-first path took over,
  Debian client launched clean. The 2026-08-28 fast-start design held.
- **Concurrent lanes coexist**: a foreign session's dual-OS lane
  (`br-laneA`, 10.99.150.x — PR #192's `--net-id` machinery in live use)
  ran simultaneously with zero address/bridge collisions. `stop-poc` is
  pidfile-scoped to `~/tollgate-virtual-lab` and did not touch it; the
  foreign VM ended on its own during the soak.
- Teardown verified: no poc QEMUs, 10.99.99.x clear, bridges down.

## Reproduction

```bash
python3 scripts/virtual-lab.py start-poc --host localhost   # re-run if serial times out
./scripts/run-local-tests.sh \
  tests/api/test_deployed_contracts.py tests/api/test_portal_verify.py \
  tests/api/test_health.py tests/api/test_train_topology_scenarios.py \
  tests/api/test_visual_happy_path.py
python3 scripts/virtual-lab.py stop-poc --host localhost
```

## What remains unexercised on this venue

The #196/#201 skips all trace to the deployed artifact's age. Deploying a
current build (CI is frozen; use `packaging/local-build-ipk.sh` per the
2026-10-06 AGENTS.md lesson) to the VM would let the contract tests and
train scenarios run for real — that, plus the physical bench, is the
remaining validation debt for the PR wave.
