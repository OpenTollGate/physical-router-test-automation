# E750 lab profile (test target)

The GL-E750 is a **test target**, not a production gateway. It is the `mips_24kc`
ath79 sibling of the MT3000/COVR bench routers.

## Hardware

```text
model       GL.iNet GL-E750 (V1)
SoC         QCA9531 / AR9531 -> ath79, mips_24kc
RAM/NAND    128 MB / 128 MB
WiFi        single 2.4 GHz radio (5 GHz ath10k is broken on this unit)
Ethernet    100 Mb/s via the port replicator
Cellular    4G MBIM modem, no SIM
Power       internal battery + USB-C charger
Recovery    serial U-Boot; U-Boot web recovery is dead over the replicator
```

## Supported OS

Either is acceptable; record it per router:

- **OpenWrt 24.10.x** — `opkg` / `.ipk`
- **OpenWrt 25.x** — `apk` / `.apk`

TollGate publishes `tollgate-wrt` for `mips_24kc` in both formats. The deploy
layer already detects `apk` vs `opkg`; the profile records the expected format so
a mismatch fails early.

## Inventory example

See `config/routers.e750.example.json`:

```json
{
  "model": "GL-E750",
  "arch": "mips_24kc",
  "openwrtTarget": "ath79/nand",
  "openwrtProfile": "glinet_gl-e750",
  "packageFormat": "apk-or-ipk",
  "capabilities": {"wifi_2g": true, "wifi_5g": false, "cellular": false}
}
```

Copy it into the gitignored `config/routers.json` with the real management host
and credentials from OpenBao.

## Enrollment

1. Put the E750 on the isolated lab segment (not the production LAN).
2. Identify by board/MAC; `scripts/check-protected.py` must say `NOT_PROTECTED`.
3. Acquire the bench lease:
   ```bash
   scripts/hw-bench-lease exec --purpose "e750 enrollment" -- bash
   ```
4. Install the fleet keys:
   ```bash
   scripts/install-fleet-keys.sh --router e750-lab
   ```
5. Install/verify the TollGate package for the detected format (`.apk`/`.ipk`).
6. Quiesce APs after the run:
   ```bash
   scripts/quiesce-router.py --router e750-lab
   ```
7. Record the OS, arch, package format, firmware image/hash, and recovery
   credentials in OpenBao `secret/fleet/test-routers-e750-lab`.

## Recovery / brick guard

- Keep `/dev/mtd1` (U-Boot env) backup and the known-good image/hash.
- The working bootcmd is:
  ```text
  cp.b 0x9f060000 0x81000000 0x400000; bootm 0x81000000
  ```
  The kernel is on SPI NOR at `0x9f060000`; `nboot` from NAND fails with
  `Bad Magic Number 0x55424923` (`UBI#`).
- U-Boot web recovery at `192.168.1.1` does **not** work over the port-replicator
  Ethernet. Use serial U-Boot.
- The internal battery means cutting charger power does not cold-boot the unit;
  unattended power-cycle recovery needs a relay/power-button path. Until that
  exists, use **package installs only**.
- Keep `TOLLGATE_ENABLE_SYSUPGRADE_TESTS=false` and
  `TOLLGATE_ENABLE_SYSUPGRADE_FLASHING=false` for this target.

## Capability skips

- No 5 GHz / 6 GHz / dual-band tests.
- No cellular tests without a SIM.
- Expect ~100 Mb/s Ethernet; raise data-allotment timeouts.
- Single radio: AP+STA coexistence is limited; prefer Ethernet upstream for
  portal tests.

## Protected devices

The MT6000 gateway (`glinet,gl-mt6000`, br-lan `94:83:c4:d1:57:56`), x280,
cobrador, dq05 and T14 are protected. `scripts/check-protected.py` refuses them;
the test framework must never include them in `config/routers.json`.
