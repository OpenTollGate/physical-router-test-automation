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

---

# Re-soak — same day, current build deployed

Artifact: tmbg main `7fec9e34` built locally as
`tollgate-wrt_v0.6.0-rc4-local_x86_64.ipk` (pinned inputs honored: node
22.17.0 sha-verified tarball, GOTOOLCHAIN go1.26.8, portal SHA `6c25834`,
`SOURCE_DATE_EPOCH` normalization). Installed onto a **fresh** (`--fresh`)
DUT overlay via `virtual-lab.py install-package`; fresh bases need
`opkg update` first (pristine 24.10 lacks nodogsplash/jq/ca-bundle — the
declared DEPENDS pull cleanly once feeds are current).

## Contract movement vs the morning soak

| Test | Morning (old artifact) | Re-soak (rc4-local) | Reading |
|---|---|---|---|
| content-type is JSON | SKIP (unparseable) | **PASS** | current artifact serves `application/json` (#730/#628 honored live) |
| version ordering | SKIP (comparator "unavailable") | **PASS** | after fixing two PRTA bugs in `_compare_versions` (below) — the target's own opkg agrees with the `~` topology (#738) |
| entry_ui port mapping | SKIP (contract absent) | **FAIL** | real drift signal — see classification below |
| postinst fail-loud (destructive) | SKIP (not opted in) | **FAIL** (empty capture) | genuine #715-class upstream signal — see below |
| train scenarios (×3) | SKIP (drift signal) | SKIP (correct) | the stop-ship train (#782-#786) is not in main's ipk — these await the train package by design |
| happy path | FAIL (environmental) | **clean SKIP** | #234's `skip_if_no_client_playwright` works; persisted Debian overlay predates its seeding |
| time-metering e2e | PASS | **PASS** | payment rail green on the current artifact too |
| health / portal suites | PASS | PASS | |

## Two PRTA test bugs found and fixed (the eternal-skip class)

1. **`_compare_versions` heredoc + stdout assumption**
   (`tests/api/test_deployed_contracts.py`): the operator was interpolated
   *unquoted* — `opkg compare-versions 'a' << 'b'` is a shell HEREDOC, opkg
   reads stdin, exits 255, and the test read "comparator unavailable"
   forever. Quoted, the second half surfaced: OpenWrt's opkg prints
   NOTHING and encodes the verdict in the exit status; the test assumed
   printed 0/1. Fixed to accept both. The #738 contract had never actually
   executed before this fix.
2. **`install-package` staged the artifact extensionless**
   (`scripts/virtual-lab.py`): opkg (like apk3, which #192 measured)
   refuses extensionless local files — "Unknown package /tmp/lane-pkg".
   The staging name now keeps the artifact's extension on both lanes.

Plus one environment-robustness fix, same root: **fresh-cycle host-key
poisoning**. A `--fresh` overlay gives the VM a new host key; ssh calls
with `StrictHostKeyChecking=no` but a real `known_hosts` still hard-fail
on CHANGED keys. `install-package`'s probe and all 11 runner ssh calls in
`run-local-tests.sh` now pass `-o UserKnownHostsFile=/dev/null` (the
pattern the rest of PRTA already used).

## entry_ui FAIL — classification: venue/packaging drift, not a test bug

`entry_ui=board` config demands uhttpd pairs (8080,443)+(8090,8443). The
VM converged to 8080/443/2051 only. Mechanism (serial + logread): the
ipk-only install lacks the captive-portal feed marker
(`92-tollgate-admin-setup`, FreedomTechFeed) that board-mode port mapping
requires; `99-tollgate-setup` converges legacy mapping at install time,
and only afterwards does the backend populate the `entry_ui=board`
DEFAULT into config.json ("Populated entry_ui default (board) on
upgrade", 19:29:05). The contract test correctly flags config-vs-reality.
Follow-ups: local venue should install the feed package for board-mode
soaks; upstream may want the default to refuse board when the marker is
absent.

## postinst husk FAIL — a real #715-class upstream signal

With `TOLLGATE_DESTRUCTIVE=1` (disposable VM venue), the husk test ran
for the first time: husked nodogsplash (`: > /usr/bin/nodogsplash`),
`opkg install --force-reinstall` — the postinst did NOT fail loud with a
remediation message. It proceeded, and the rc4 setup moved eth0 off
br-lan onto **br-private** (kernel log proof in the serial console) —
severing the lab network mid-install; ssh died, the capture came back
empty, and the VM stranded itself off 10.99.99.x. Two consequences:
  - **Upstream (tmbg, #715)**: install over a dead portal neither refuses
    nor names the husk — it re-architects networking. The fail-loud
    contract is not honored by rc4.
  - **PRTA follow-up**: the husk test cannot observe its verdict when the
    channel dies mid-install — it needs `RouterUnreachable`-aware retry
    (TOLLGATE_ALLOW_ROUTER_MOVE + re-target) or a serial-console capture
    path. Recovery was `stop-poc && start-poc --fresh` + redeploy.

**Post-script (same day, follow-up run on an overlay without a private
network — there the br-private move degrades to a warning and the channel
survives): the full capture closed every caveat. The postinst printed
"Post-installation script completed successfully" and opkg exited RC=0
over the zero-byte nodogsplash — the silent-death class proven outright.
Filed on tmbg #715 (both comments) and tmbg #928 (the entry_ui
default-vs-convergence sibling). The PRTA test now classifies the
severed-channel case as its own loud failure naming the mechanism,
instead of tripping on an empty capture; its teardown tolerates an
unreachable router so recovery cannot mask the verdict.**
