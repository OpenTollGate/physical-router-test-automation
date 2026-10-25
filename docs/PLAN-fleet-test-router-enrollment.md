# Fleet test-router enrollment and x280 bench plan

Status: **complete** (2026-10-26). This is the final handoff record. Live
operations are documented in `docs/FLEET-TEST-ROUTERS.md` and
`docs/X280-BENCH.md`; the conwrt device-model work is in
`docs/PLAN-conwrt-comfast-cf-wr632ax.md`.

## Goal

Make x280 the single test manager and bench bastion for the lab routers, enroll
the E750, COVR-X1860, GL-MT3000 and COMFAST as test targets, keep the MT6000
production gateway out of the test set, and guarantee APs are quiesced after
every test.

## Non-negotiable

- MT6000 (`glinet,gl-mt6000`, `192.168.50.1`, br-lan
  `94:83:c4:d1:57:56`) is production: never in the test inventory, never
  flashed/reconfigured/tested.
- T14 is a personal control console: no test workloads, no Playwright, no
  pytest, and no router key material.
- x280 is the only host on the bench links and the only host that runs the test
  suite and the bench lease.
- Every test entrypoint requires the machine-global bench lease.
- Router private keys and router passwords live in OpenBao only. The
  lease-scoped bench key is materialized only for a leased process and removed
  afterward. Nothing secret is committed to git.
- APs are disabled by default and after every test; a watchdog on x280 quiesces
  when the bench lease is free.
- The router root/LuCI password is retained without rotation for LuCI/recovery
  and is stored as `luci_password` in `secret/fleet/test-routers-*`; it is not
  written in this repository.

## Final verified state

```text
MT6000      glinet,gl-mt6000, 192.168.50.1; hermes-lan radio0 ch11;
            OMR verify ok; protected/not in test inventory; T14-only SSH
T14         hermes-lan 192.168.50.160; wired reservation .161; control console
x280        fleet 192.168.50.10; bench manager; OpenBao AppRole client;
            routes + quiesce watchdog active
E750        OpenWrt 25.12.5; LAN 192.168.8.1; serial by-id BH00DL3M;
            STA to hermes-lan; TollGate-E750 disabled; key-only, 11 keys
COVR-X1860  OpenWrt 25.12.5; WAN 192.168.50.218; LAN 192.168.77.1;
            private 192.168.78.1; serial by-id BH00D6DL; key-only, 11 keys
GL-MT3000   OpenWrt 25.12.5; private 10.110.223.1 via eth1; LAN 192.168.1.1;
            serial by-id BG046H5F; key-only, 11 keys
COMFAST     CF-WR632AX; OpenWrt 25.12.5; physical 2.5G WAN = netdev wan,
            192.168.50.219; LAN br-lan 192.168.1.1; serial by-id BG046H14;
            key-only, 11 keys; TollGate-COMFAST APs disabled
OpenBao     AppRole physical-router-automation; test-router secrets only;
            policy denies secret/fleet/mt6000-*
```

All four test routers:

- have 11 public keys (10 fleet + the lease-scoped x280 bench key);
- have x280's general `id_fleet`/`id_ed25519` deliberately absent;
- have `PasswordAuth=off` and `RootPasswordAuth=off`;
- report 0 APs when quiesced;
- have their root/LuCI password in OpenBao, not git.

## Access and mutex

The machine-global bench lease is the only mutex:

```bash
cd /home/c08r4d0r/src/prta-main
scripts/hw-bench-lease exec --purpose "test-pr 42" -- \
  scripts/prta-bench-run.sh -- ./scripts/test-pr.sh --pr 42
```

`prta-bench-run.sh` refuses without the lease, fetches the bench key from
OpenBao, exports `TOLLGATE_SSH_KEY`, runs the test, quiesces all APs, and deletes
the temporary key. The 2-minute x280 watchdog also quiesces when the lease is
free.

Residual risk: root on x280 can bypass the wrapper. A dedicated lock
daemon/user is future hardening.

## AP lifecycle

Tests arm APs for the leased run and always quiesce afterward:

```bash
python3 scripts/quiesce-router.py --router <id> --enable --allow-unlocked
# ... test ...
python3 scripts/quiesce-router.py --router <id> --allow-unlocked
```

The pytest `sessionstart` hook, `scripts/test-pr.sh`, and
`scripts/run-with-quiesce.sh` call `--enable`; the pytest `sessionfinish` hook,
`test-pr.sh`'s EXIT trap, `prta-bench-run.sh`, and the watchdog call the quiesce
path. COMFAST was verified to go 0 -> 2 APs during a leased run and back to 0.

## Infrastructure as code

- Automation: `OpenTollGate/physical-router-test-automation`, default `main`.
  Contains the router inventory template, fleet keys, mutex/bench wrapper,
  quiesce/arm logic, x280 deployment, and the reproducible COMFAST post-flash
  config (`scripts/bench/comfast_apply.sh`, `make comfast-apply`).
- Gateway: bare `master` (and `feat/mt6000-mptcp-gateway`) at `08d5fee92`.
  Contains the MT6000 OMR IaC and the COMFAST `.219` DHCP reservation.
- conwrt: PR https://github.com/Amperstrand/conwrt/pull/567, branch
  `feat/comfast-cf-wr632ax-tested` at `e950d95`; model validation and all CI
  checks pass. The PR is open and mergeable, awaiting maintainer merge.
- x280 tracks `origin/main` and runs
  `deploy/x280/install-bench.sh`; the live `config/routers.json` is the
  gitignored instance of `config/routers.fleet.example.json`.

Useful commands:

```bash
# reproduce the COMFAST OpenWrt target config
scripts/hw-bench-lease exec --purpose "comfast apply" -- \
  scripts/prta-bench-run.sh -- scripts/bench/comfast_apply.sh

# verify production protection and gates
python3 scripts/check-protected.py --address 192.168.50.1
make -C /home/c08rad0r/worktrees/mt6000-mptcp-gateway mt6000-omr-verify
make -C /home/c08rad0r/worktrees/mt6000-mptcp-gateway mt6000-canary-gate
```

## Verification evidence

```text
MT6000 OMR verify          VERIFY: ok
MT6000 canary              7/3 READY
COMFAST canary             lease=PASS reach=PASS dns=PASS UP
COMFAST apply              network-changed -- 0; 0 APs; 11 keys; password off
COMFAST AP cycle           0 -> 2 -> 0
Protected check            MT6000 REFUSED; no MT6000 in inventory
```

## Follow-ups (not blockers)

- **conwrt PR #567** is open and mergeable with all required checks green
  (`changes`, `rust`, `firmware-esp32s3`, `test` 3.10-3.13). It is an upstream
  PR; a conwrt maintainer still needs to merge it.
- **COMFAST LAN L2**: the 1G LAN port links at 1 Gbps, but it is not wired to
  an x280 adapter. Management is the 2.5G WAN with an x280-only firewall. For
  full L2 isolation, wire LAN to `enx9c69d3c70689`, give x280
  `192.168.1.3/24`, and move management to `192.168.1.1`.
- **COVR L2**: still managed over WAN `192.168.50.218` with an x280-only rule.
  A direct cable from COVR `br-private` (`192.168.78.1`) to a spare x280
  adapter (`192.168.78.2/24`) would complete direct L2 management.
- **tollgate-wrt**: E750 and COMFAST do not have it preinstalled; the test
  framework's deploy step resolves and installs the matching `.apk` at run
  time. COVR and MT3000 already run `tollgate-wrt-0.6.0_rc6-r1`.
- **t14-wired** canary is DOWN because `enp0s31f6` has no cable. The canary
  still passes at 7/3; `dq05-wifi` also fails reach but is not required.
- **COMFAST recovery**: U-Boot recovery and the physical reset-button wipe were
  not exercised; serial U-Boot is the documented recovery path.
