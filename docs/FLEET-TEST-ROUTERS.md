# Fleet test routers, bench topology, and mutex

Status: **live** (2026-10-25). x280 is the only test manager; T14 is a control
console; the MT6000 is production infrastructure.

## Topology

```text
T14 (hermes-lan 192.168.50.160) --ssh--> x280 (192.168.50.10)
                                          |
                                          |-- enx9c69d3ca3cfe  192.168.8.244/24
                                          |     E750 192.168.8.1  (LAN)
                                          |
                                          |-- enx6c6e0750c33b  10.110.223.2/24
                                          |     MT3000 10.110.223.1 (br-private / eth1)
                                          |
                                          |-- COVR 192.168.50.218 (WAN)
                                                x280-only firewall rule
```

- T14 runs no tests, no pytest, no Playwright, and holds no router key material.
- x280 owns the bench links, the machine-global bench lease, and the
  `physical-router-automation` OpenBao AppRole.
- The MT6000 is not on any bench link and is not in the test inventory.

## Routers

| ID | Model | Management | OS / arch | APs | Notes |
|---|---|---|---|---|---|
| `e750-lab` | GL.iNet GL-E750 | `192.168.8.1` via x280 | 25.12.5, `mips_24kc` / `ath79/nand` | `TollGate-E750` (disabled) | 2.4 GHz STA to `hermes-lan`; no 5 GHz/cellular |
| `tollgate-af92` | D-Link COVR-X1860 A1 | `192.168.50.218` WAN | 25.12.5, `mipsel_24kc` / `ramips/mt7621` | TollGate + private (disabled) | Serial `ttyUSB2`; x280-only WAN rule |
| `tollgate-9bd4` | GL.iNet GL-MT3000 | `10.110.223.1` private | 25.12.5, `aarch64_cortex-a53` / `mediatek/filogic` | TollGate + private (disabled) | Serial `ttyUSB3`; LAN is `br-private` |
| `comfast-b3ba` | COMFAST | `192.168.50.148` (pending reset) | unknown | `COMFAST_B3BA_2G/5G` | AP-only target; legacy `ssh-rsa` |

All three enrolled routers have:

- 12 fleet public keys in `/etc/dropbear/authorized_keys`;
- `PasswordAuth=off`, `RootPasswordAuth=off`;
- root password `c03rad0r123` retained for LuCI/recovery (no rotation);
- all AP interfaces disabled after enrollment (`quiesce` reports 0 APs).

Secrets live in OpenBao:

```text
secret/fleet/test-routers-e750-lab
secret/fleet/test-routers-tollgate-af92
secret/fleet/test-routers-tollgate-9bd4
secret/fleet/test-routers-comfast-b3ba   # when enrolled
```

Fields: `ssh_password`, `luci_password`, `ssh_key_path`. Never commit values.

## MT6000 protection

The MT6000 (`glinet,gl-mt6000`, `192.168.50.1`, br-lan
`94:83:c4:d1:57:56`, hostname `OpenMPTCProuter`) is in
`config/protected_devices.yaml` and is deliberately absent from the test
inventory. `scripts/check-protected.py` refuses it, `quiesce-all` skips it, and
it keeps T14-only SSH with no fleet keys.

## Mutex / access policy

The bench lease is the only mutex. Every entrypoint refuses without it:

```bash
cd /home/c08r4d0r/src/prta-main
scripts/hw-bench-lease exec --purpose "test-pr 42" -- \
  scripts/prta-bench-run.sh -- ./scripts/test-pr.sh --pr 42
```

`prta-bench-run.sh`:

1. verifies the bench lease;
2. logs in to OpenBao with the AppRole;
3. materializes `secret/fleet/test-routers-bench:ssh_private_key` in a 0600
   temporary file and exports `TOLLGATE_SSH_KEY`;
4. removes the key and runs `quiesce-all` on exit.

The L2 layer is separate: routers are reachable only from x280's bench
adapters or the x280-only WAN rule. Other fleet hosts can still hold keys, but
they have no route to the management plane. T14 uses `ssh -J x280` for manual
intervention.

Residual risk: a root shell on x280 can bypass the wrapper; IP spoofing on the
flat fleet LAN could bypass the COVR WAN rule until it is moved to a direct
bench link. A dedicated bench VLAN or lock-service bastion is the next
hardening step.

## AP quiesce

`scripts/quiesce-router.py` disables every `mode=ap` interface and reloads
WiFi; STA/LAN management is untouched. It is called by:

- the pytest `sessionfinish` hook;
- `scripts/test-pr.sh` on exit;
- `scripts/prta-bench-run.sh` on exit;
- `scripts/prta-quiesce-watchdog.sh` every 2 minutes when the lease is free.

A router that cannot be quiesced must be marked broken and, if possible, have
its power or switch port cut. The E750's battery means cutting the charger does
not cold-boot it.

## x280 installation

See [`X280-BENCH.md`](X280-BENCH.md).

## Verification

```bash
# from x280
cd /home/c08r4d0r/src/prta-main
python3 scripts/check-protected.py --address 192.168.50.1   # REFUSED
python3 scripts/check-protected.py --address 192.168.8.1    # NOT_PROTECTED
python3 scripts/quiesce-router.py --all --allow-unlocked    # QUIESCE_DONE, 0 APs
scripts/hw-bench-lease status                               # free/held
```

Key login (inside a lease):

```bash
scripts/hw-bench-lease exec --purpose verify -- \
  bash -c 'scripts/prta-bench-run.sh -- ssh -i "$TOLLGATE_SSH_KEY" root@192.168.8.1 "echo E750_OK"'
```

Password auth must fail:

```bash
sshpass -p c03rad0r123 ssh -o PubkeyAuthentication=no \
  -o PreferredAuthentications=password root@192.168.8.1 true
# Permission denied (publickey)
```

## E750 notes

- 25.12.5 sysupgrade image:
  `openwrt-25.12.5-ath79-nand-glinet_gl-e750-squashfs-sysupgrade.bin`
  sha256 `78d24f6da5da1785d57cdfe7e98976cd53651d61e5d460919e6219a9e4ae3a16`.
- `/etc/fw_env.config` must exist:
  `/dev/mtd1 0x0 0x10000 0x10000`.
- bootcmd:
  `cp.b 0x9f060000 0x81000000 0x400000; bootm 0x81000000`.
- 2.4 GHz STA to `hermes-lan` (WPA2 PSK) gives the E750 internet through the
  MT6000 OMR path; the test AP is `TollGate-E750`, disabled by default.
- U-Boot web recovery does not work over the port replicator; serial U-Boot is
  the recovery path. Keep `e750-mtd1-backup.bin` and the config backup.
- Serial TX is marginal; use the LuCI/ubus RPC if dropbear breaks.

## COMFAST status

The COMFAST is powered and reachable at `192.168.50.148`, but its console and
SSH reject `root`/`admin` with `c03rad0r123`, blank, and factory defaults. It
needs a physical factory reset (hold reset ~10 s). After reset:

1. log in over `/dev/ttyUSB1`;
2. record `ubus call system board` and `/etc/openwrt_release`;
3. if standard OpenWrt, enroll like the others; otherwise keep it AP-only
   (`tollgate: false`) and skip package/API tests;
4. if its SSIDs cannot be quiesced, leave it out until they can.
