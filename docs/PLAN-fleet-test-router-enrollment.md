# Fleet test-router enrollment and x280 bench plan

Status: **implementing** (2026-10-25). Supersedes the earlier integration checklist
for the current phase.

## Goal

Make x280 the single test manager and bench bastion for the lab routers, enroll
the E750, COVR-X1860, GL-MT3000 and COMFAST as test targets, keep the MT6000
production gateway out of the test set, and guarantee APs are quiesced after
every test.

## Non-negotiable

- MT6000 (`glinet,gl-mt6000`, `192.168.50.1`, br-lan `94:83:c4:d1:57:56`) is
  production: never in the test inventory, never flashed/reconfigured/tested.
- T14 is a personal control console: no test workloads, no Playwright, no
  pytest, no router keys materialized outside the x280 lease.
- x280 is the only host allowed on the bench links and the only host that runs
  the test suite and the bench lease.
- Test-router SSH/LuCI on WAN is blocked. Management is direct to x280:
  - E750 `192.168.8.1` via `enx9c69d3ca3cfe` (`192.168.8.244/24`).
  - MT3000 `10.110.223.1` via `enx6c6e0750c33b` (`10.110.223.2/24`).
  - COVR `192.168.78.1` via a spare x280 adapter (`192.168.78.2/24`).
  - COMFAST LAN via a spare x280 adapter, or WAN with an x280-only firewall if
    it has no usable LAN port.
- Every test entrypoint requires the machine-global bench lease.
- Router private keys live in OpenBao and are materialized only for a leased
  test process, then removed.
- APs are disabled by default and after every test; a watchdog on x280 quiesces
  when the bench lease is free.
- `c03rad0r123` is retained; no rotation. Secrets only in OpenBao, never git.

## Current state (verified)

```text
T14            hermes-lan 192.168.50.160; direct ssh root@192.168.50.1
x280           fleet 192.168.50.10; FIPS; test venv; no bao; no E750 key
E750           OpenWrt SNAPSHOT r33865; 192.168.8.1; key-only; 12 fleet keys;
               APs quiesced; OpenBao secret test-routers-e750-lab exists
COVR-X1860     25.12.5 r33051; WAN 192.168.50.218; LAN 192.168.77.1;
               private 192.168.78.1; serial x280 ttyUSB2; 0 keys
GL-MT3000      25.12.5 r33051; WAN 192.168.50.205; LAN 192.168.1.1 (WiFi only);
               private 10.110.223.1 on eth1; serial x280 ttyUSB3; 0 keys
COMFAST        dropbear, only tcp/22; 192.168.50.148; SSIDs COMFAST_B3BA_2G/5G;
               credentials unknown; needs factory reset
OpenBao        T14 loopback + FIPS ULA; policy physical-router-automation exists;
               AppRole enabled; role not created
E750 image     ~/e750-fw.bin 7,885,072 bytes sha256 80357e82...e4a1d
```

## Work plan

### 1. x280 manager

- [ ] Clean checkout of `origin/main` at `cf1b29d` (separate from the dirty
      `~/src/physical-router-test-automation`).
- [ ] Copy `bao` from T14; set `BAO_ADDR` to T14's FIPS ULA.
- [ ] Create AppRole `physical-router-automation` with the existing policy,
      15-minute tokens and a 24-hour SecretID; store role ID non-secret and
      SecretID in `~/.config/prta/` mode 0600.
- [ ] Add `scripts/openbao-approle-login.sh` to the automation repo.
- [ ] Private `config/routers.json` for `e750-lab`, `tollgate-af92`,
      `tollgate-9bd4`, `comfast-b3ba`; no secrets.
- [ ] Use `~/.tollgate-test-venv` (pytest/yaml/requests present).
- [ ] Quiesce watchdog: every 2 minutes, if the bench lease is free, run
      `quiesce-all`.

### 2. Bench links

- [ ] MT3000: `ip link set enx6c6e0750c33b up`, add `10.110.223.2/24`, verify
      `ping 10.110.223.1` and SSH.
- [ ] COVR: connect Ethernet (`br-private` 192.168.78.1) to a spare x280 adapter,
      add `192.168.78.2/24`, verify. Fallback: WAN 192.168.50.218 with an
      x280-only firewall.
- [ ] E750: already direct.
- [ ] COMFAST: factory reset; manage via LAN direct link or WAN x280-only rule.

### 3. Enroll COVR and MT3000

Via serial from x280:

- [ ] Install the 12 fleet public keys (`config/fleet_ssh_keys.yaml`) into
      `/etc/dropbear/authorized_keys` and `/root/.ssh/authorized_keys`.
- [ ] Set root password `c03rad0r123` for LuCI; disable SSH password auth.
- [ ] Allow TCP 22/443 only from x280's bench address; block WAN management.
- [ ] Verify key login from x280; verify password auth fails.
- [ ] Quiesce all AP interfaces; verify 0 APs.
- [ ] Store secrets in OpenBao `secret/fleet/test-routers-<id>`.
- [ ] Add inventory entries with model, arch, target, profile, MACs, serial,
      capabilities, recovery.
- [ ] Record baseline and add a known-good config backup.

### 4. E750 to 25.12.5 + TollGate

- [ ] Copy the verified image to x280; verify sha256.
- [ ] `sysupgrade -n` with serial `ttyUSB0` attached; keep the factory image and
      `/dev/mtd1` env backup.
- [ ] After reboot: confirm `192.168.8.1`, set root password, install fleet keys,
      key-only SSH, restrict to `192.168.8.244`.
- [ ] Confirm the bootcmd is still
      `cp.b 0x9f060000 0x81000000 0x400000; bootm 0x81000000`.
- [ ] Install the 25.12.5 `mips_24kc` `tollgate-wrt` `.apk` and dependencies.
- [ ] Configure a 2.4 GHz AP with StarGate STA upstream; package-only tests.
- [ ] Quiesce after every run. No further sysupgrade after baseline.

### 5. COMFAST

- [ ] Factory reset (physical button; serial console has no valid credential).
- [ ] Log in over `ttyUSB1`; capture `ubus call system board` and
      `/etc/openwrt_release`.
- [ ] If standard OpenWrt: enroll like the others. If a vendor fork: enroll as
      an AP-only target (`tollgate: false`) and skip package/API tests.
- [ ] If its SSIDs cannot be controlled, leave it out until they can; it cannot
      satisfy the AP-off guarantee.

### 6. Mutex / bastion

- [ ] L2 isolation: test routers reachable from x280's bench adapters only;
      WAN SSH/LuCI blocked.
- [ ] Bench lease required by every entrypoint; keys only materialized under
      lease.
- [ ] Quiesce watchdog; stale-router quarantine/alert.
- [ ] T14 uses `ssh -J x280` for intervention; no direct bench route.
- [ ] Document the residual risk: root on x280 can bypass the wrapper; a
      dedicated lock daemon/user is future hardening.

### 7. Verify and merge

- [ ] E750, COVR, MT3000 key login; password auth rejected.
- [ ] `quiesce-all` reports 0 APs on every enrolled router.
- [ ] Protected check refuses the MT6000 and control hosts.
- [ ] Inventory has no MT6000.
- [ ] Test without the lease is refused; with the lease it runs and quiesces.
- [ ] Automation PR merged to `origin/main`; gateway IaC pushed to bare
      `master`/`feat`/`pr`.


## Final status (2026-10-25)

Implemented and live:

- MT6000 `hermes-lan` AP restored on radio0/ch11; t440, T14 and a Pixel are
  associated; 5 GHz StarGate and the OMR tunnel are untouched.
- T14 is on `hermes-lan` at `192.168.50.160`; wired reservation `.161`.
- E750 flashed to OpenWrt 25.12.5, bootcmd verified, `fw_env.config` restored,
  2.4 GHz STA to `hermes-lan` (`192.168.50.123`), `TollGate-E750` AP disabled,
  key-only SSH with the 11-key set.
- COVR-X1860 and GL-MT3000 enrolled on 25.12.5: key-only, 11 keys, x280-only
  management, all APs quiesced, OpenBao secrets stored.
- OpenBao AppRole `physical-router-automation` created; policy is test-router
  only and denies `secret/fleet/mt6000-*`.
- Bench key: public half in `config/fleet_ssh_keys.yaml` under `bench`; private
  half in `secret/fleet/test-routers-bench`; no local private key remains.
- Mutex verified: unleased access is refused; the lease wrapper fetches the key,
  runs the command, quiesces all APs, and removes the key.
- x280 routes and the 2-minute quiesce watchdog are installed from
  `deploy/x280/install-bench.sh`.
- MT6000 OMR verify: `VERIFY: ok`; canary: `6/3 READY`.
- Merged: automation `origin/main` `0d68073`; gateway bare `master`
  `eee24247c` (plus feat/pr/manager).

Pending:

- **COMFAST** still needs a physical factory reset (hold reset ~10 s). Serial
  `/dev/ttyUSB1` is present but every credential is rejected. Then enroll it as
  an AP-only target unless `ubus call system board` shows a supported OpenWrt
  target.
- E750 `tollgate-wrt` is not installed yet; the test framework's deploy step
  resolves and installs the `mips_24kc` `.apk` at run time. COVR/MT3000 already
  run `tollgate-wrt-0.6.0_rc6-r1`.
- COVR still uses its WAN with an x280-only rule; a direct LAN/private cable to
  a spare x280 adapter would complete the L2 bench for that router.
