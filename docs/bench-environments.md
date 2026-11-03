# Bench environments — which routers are ours, where they live, what runs where

Written 2026-10-01 after a live session confused three different MT3000
populations and nearly built infrastructure for the wrong one. If a doc,
report, or session mentions a router, FIRST place it in one of the three
environments below. When in doubt: the SSID suffix is the discriminator on
TollGate APs (`TollGate-XXXX`), and `scripts/bench/device-identity.sh` is
the fail-closed pin for anything destructive.

## Environment 1 — labgrid PoE bench (the farm)

**Where:** ai-legion-small (physical DUT host per the host-role law); the
labgrid coordinator is the canonical one on ai-legion.

**Devices:** 2 PoE routers, 2× x1860, a fleet of WS3915i — all powered and
**power-controlled via PoE** (remote power cycling is the farm's defining
capability).

**What runs here:** farm-scheduled device testing — anything that needs
power control, unattended re-runs, or shared allocation. This is the only
environment where devices are always-on shared resources.

**Access:** labgrid places via the coordinator. Nothing here depends on a
laptop or a human bridge.

## Environment 2 — laptop bench (mt3000-bench, the live/dev rig)

**Where:** the field laptop's desk. The laptop is dual-homed: ethernet
on the router LAN (DHCP — historically `.2`, observed `.127` on
2026-09-30; don't hardcode the address) + lab wifi.

**Devices:** the **GL-MT3000 "TollGate-326D" @ 192.168.1.1** (OpenWrt
24.10, aarch64_cortex-a53 — MediaTek MT7981, same model family as Env 3's
units but a DIFFERENT device), plus the Cudy WR3000 hazard documented in
[bench-device-identity.md](bench-device-identity.md) (two boxes answering
on `192.168.1.1` — identity pinning is mandatory before destructive steps).

**What runs here:** the commissioned live API/SSH tier (PRTA with
`TOLLGATE_SSH_HOST=192.168.1.1`), portal/captive probes, and the on-device
fakewallet-mint work. AI/SSH only unless a commission says otherwise.

**Access:** ONLY through the laptop — ai-legion has no route to
192.168.1.x (verified repeatedly). Today that means either running on the
laptop (see `/tmp/laptophandoff` convention on ai-legion) or a reverse
tunnel the laptop opens. **Enrollment in overlays (NetBird etc.) is NOT
sanctioned for this environment** — owner decision 2026-10-01; if remote
access is ever wanted, propose it, don't build it.

## Environment 3 — external NetBird MT3000s (NOT OURS)

**What:** two GL.iNet MT3000 (arm64, OpenWrt 24.10.4) on a NetBird overlay
(`100.90.41.166` / `100.90.216.248`, AP SSIDs `TollGate-1690` and
`TollGate-D1C6`, aliases router-alpha/beta).

**Status: OUT OF SCOPE.** Do not test on them, do not enroll anything of
ours with them, do not cite them as reachable infrastructure. They appear
in older WiFi/upstream reports (`upstream-wifi-test-report.md`,
`router-b-incident-2026-04-30.md`, `TEST_SETUP.md`) — when a session finds
"MT3000 + arm64 + NetBird" in a doc, THAT is this environment, never
Environment 2's router.

## Environment 4 — the serial fleet bench (TollGate-installed)

**Where:** the serial fleet hosts. MT3000 on **T440** (`/dev/ttyUSB0`), MT6000 on
**x280** (`/dev/ttyUSB1`; `ttyUSB0` is the other adapter on that host) — FTDI
USB-TTL, 115200 8N1, 3.3 V. The gateway (`cobrador`) reaches both over USB-eth.

**Devices:** the **TollGate-installed** pair — MT3000 hostname `tollgate-52BE`
(OpenWrt 25.12.5, apk, `aarch64_cortex-a53`), MT6000 hostname `tollgate-A04E`
(OpenWrt 24.10.2, opkg). These are the boxes a package under test is flashed onto.

**What runs here:** serial-console capture (flash, boot log, recovery) and
anything that must reach the box's **post-install** services — the captive
portal and the board.

**Access:** the gateway on the box's **management net** (`10.x`); serial console
from T440/x280. **The flashed LAN IP is gone after install** — the wired LAN
moves to br-private, so `192.168.1.1` routes out the WAN and is firewalled. See
[RUNBOOK-bench-lab.md](RUNBOOK-bench-lab.md#reaching-a-tollgate-installed-box-post-install-address)
for the `ip route get` discovery recipe. From `cobrador` the MT3000 answers on
`10.57.113.1` (portal `:2051`, board `https://…/`).

## Environment 5 — the GL-E750 "Mudi" (x280, SSH + capture-only serial)

**Device:** GL.iNet **GL-E750**, board `glinet,gl-e750`, `ath79/nand`,
`mips_24kc`, br-lan MAC **94:83:c4:4d:fc:38**. OpenWrt 25.12.5
(`r33051-f5dae5ece4`), kernel 6.12.94. Battery-powered.

**Where:** on **x280**, wired to `enx9c69d3ca3cfe` (x280 holds 192.168.8.244/24)
at **192.168.8.1**; serial on FTDI **`BH00DL3M`** (115200 8N1).

**Venue file:** [`configs/labgrid/e750.yaml`](../configs/labgrid/e750.yaml)

```bash
pytest --lg-env=configs/labgrid/e750.yaml --no-deploy <test>
```

**Access:** key auth only — `ssh -i ~/.ssh/id_bench root@192.168.8.1`. The
fleet's `id_ed25519`, `id_fleet` and `id_dq05` are **refused** by this unit.

**Two traps, both measured:**

1. **The serial console is TX-ONLY.** Its output arrives byte-clean, but
   nothing you send reaches the unit: with `cat /dev/ttyS0 > /tmp/rx.txt`
   running on the box, a marker transmitted from the FTDI never appeared.
   `/proc/consoles` agrees (`ttyS0 -W-`, write-only). So the serial link is
   **capture only** (boot logs, panics, recovery evidence) — there is no shell
   over it, and an askfirst nudge will not produce a prompt. Mutate via SSH.
2. **It has ONE radio, not two.** The 5 GHz QCA9887 never probes
   (`could not probe fw (-22)`), so there is no `phy0` and no 5 GHz interface at
   all. The surviving 2.4 GHz ath9k (`phy1`) is currently a **client** on SSID
   `hermes-lan`, not an AP. Do not assume a second radio or a serving AP.
   Diagnosis: felixfelix-bot/e750-repair (`docs/EVIDENCE.md` §3).

**No PowerPort:** power-cycling is a manual bench action.

## The confusion this doc exists to kill

| If a session sees... | It is... | Verdict |
|---|---|---|
| `TollGate-326D` / `192.168.1.1` | Env 2 (laptop bench) | Ours; test via laptop bridge |
| `TollGate-1690` / `TollGate-D1C6` / `100.90.x` | Env 3 (external NetBird) | Out of scope |
| WS3915i / x1860 / PoE control | Env 1 (labgrid farm) | Ours; use the coordinator |
| `tollgate-52BE` / `tollgate-A04E`, `10.57.113.1` / `10.63.101.1`, serial on T440/x280 | Env 4 (serial fleet bench) | Ours; reach on the **management net**, not `192.168.1.1` |
| `192.168.1.1` **from the gateway** | post-install box (Env 4) at a dead address | routes out the WAN — use `ip route get` |
| `glinet,gl-e750` / `192.168.8.1` / `mips_24kc` / br-lan `94:83:c4:4d:fc:38` | Env 5 (E750 Mudi on x280) | Ours; `ssh -i ~/.ssh/id_bench`; serial is capture-only |
| An E750 showing only `phy1` and no `phy0` | Env 5, expected for this unit | Not a fault — the 5 GHz QCA9887 never probes; ONE radio only |
| "MT3000 (arm64)" with no other identity | AMBIGUOUS — must be pinned before use | Identity check mandatory |

## Bench lane tooling — the x280 serial fleet, measured 2026-10-26

Complements Environment 4/5 (and PR #316): the same serial fleet, now with a
committed enrollment contract, a probe that proves each unit is what it claims,
and working SSH alongside the consoles.

**The measured map (resolve by FTDI serial, never by index — the index drifts on
replug):**

| FTDI serial | Unit | Hostname | Identity | SSH (bench key) |
|---|---|---|---|---|
| `BG046H14` | COMFAST CF-WR632AX | `comfast-b3ba` | OpenWrt 25.12.5, `aarch64_cortex-a53`, mediatek/filogic | `root@192.168.50.219` |
| `BG046H5F` | GL-MT3000 | `tollgate-9BD4` | OpenWrt 25.12.5, `aarch64_cortex-a53`, **`tollgate-wrt` installed** | `root@10.110.223.1` (br-private; the WAN-side `192.168.50.205` refuses :22) |
| `BH00D6DL` | D-Link COVR-X1860 A1 | `tollgate-AF92` | OpenWrt 25.12.5, `mipsel_24kc`, **`tollgate-wrt` installed** | `root@192.168.50.218` |
| `BH00DL3M` | GL-E750 (Mudi) | `e750-lab` | OpenWrt 25.12.5, `mips_24kc`, ath79/nand | `root@192.168.8.1` (console **dark**) |

Three state changes worth recording against older docs:

- The COVR of Environment 5 is **no longer onboarding-only**: it runs
  `tollgate-wrt` (hostname `tollgate-AF92`), so it is a real mipsel DUT now.
- PR #316's "serial is the only way in" no longer holds for these units: the
  bench publickey (`~/.ssh/id_bench` on the bench host, overridable via
  `TOLLGATE_BENCH_KEY`) is enrolled in dropbear on all four live units. Serial
  remains the recovery path where the console is wired.
- The previously "DARK" FTDI `BH00DL3M` is the **GL-E750** (`e750-lab`, stock
  25.12.5, mips_24kc). Its console is electrically silent at every baud
  (9600–1500000: floating-RX noise, no printable bytes) — the adapter is not on
  a live UART. It is enrolled **network-only** (`console_enrolled: false`), so
  the probe reports the console as not wired instead of failing the unit.

**The contract and the probe** (the committed source of truth — no ad-hoc
console scripts for evidence runs):

- `configs/labgrid/x280-serial-fleet.json` — enrollment + identity contract,
  including the DARK port (kept visible, not silently dropped).
- `configs/labgrid/x280-serial-fleet.yaml` — local-mode labgrid env (no
  coordinator on this bench; the farm's coordinator is not reachable from the
  serial fleet hosts).
- `scripts/bench/serial-fleet-probe.py` — proves console + SSH + identity per
  enrolled unit; non-zero exit if any enrolled unit fails. Run it before and
  after every evidence session.
- `tests/bench/test_serial_fleet_health.py` — the pytest gate over the same
  contract; skips cleanly on hosts without FTDI consoles.

**No power control on this bench.** The USB hubs switch the *adapters*, not the
routers. A power cycle is a manual bench action; tests must not assume one, and
the env deliberately declares no PowerPort.

## The confusion this doc exists to kill

| If a session sees... | It is... | Verdict |
|---|---|---|
| `TollGate-326D` / `192.168.1.1` | Env 2 (laptop bench) | Ours; test via laptop bridge |
| `TollGate-1690` / `TollGate-D1C6` / `100.90.x` | Env 3 (external NetBird) | Out of scope |
| WS3915i / x1860 / PoE control | Env 1 (labgrid farm) | Ours; use the coordinator |
| `tollgate-52BE` / `tollgate-A04E`, `10.57.113.1` / `10.63.101.1`, serial on T440/x280 | Env 4 (serial fleet bench) | Ours; reach on the **management net**, not `192.168.1.1` |
| `192.168.1.1` **from the gateway** | post-install box (Env 4) at a dead address | routes out the WAN — use `ip route get` |
| "MT3000 (arm64)" with no other identity | AMBIGUOUS — must be pinned before use | Identity check mandatory |

## Bench lane tooling — the x280 serial fleet, measured 2026-10-26

Complements Environment 4/5 (and PR #316): the same serial fleet, now with a
committed enrollment contract, a probe that proves each unit is what it claims,
and working SSH alongside the consoles.

**The measured map (resolve by FTDI serial, never by index — the index drifts on
replug):**

| FTDI serial | Unit | Hostname | Identity | SSH (bench key) |
|---|---|---|---|---|
| `BG046H14` | COMFAST CF-WR632AX | `comfast-b3ba` | OpenWrt 25.12.5, `aarch64_cortex-a53`, mediatek/filogic | `root@192.168.50.219` |
| `BG046H5F` | GL-MT3000 | `tollgate-9BD4` | OpenWrt 25.12.5, `aarch64_cortex-a53`, **`tollgate-wrt` installed** | `root@10.110.223.1` (br-private; the WAN-side `192.168.50.205` refuses :22) |
| `BH00D6DL` | D-Link COVR-X1860 A1 | `tollgate-AF92` | OpenWrt 25.12.5, `mipsel_24kc`, **`tollgate-wrt` installed** | `root@192.168.50.218` |
| `BH00DL3M` | DARK | — | garbage bytes at 115200 (wrong baud or unpowered) | — |

Two state changes worth recording against older docs:

- The COVR of Environment 5 is **no longer onboarding-only**: it runs
  `tollgate-wrt` (hostname `tollgate-AF92`), so it is a real mipsel DUT now.
- PR #316's "serial is the only way in" no longer holds for these units: the
  bench publickey (`~/.ssh/id_bench` on the bench host, overridable via
  `TOLLGATE_BENCH_KEY`) is enrolled in dropbear on all three live units. Serial
  remains the recovery path.

**The contract and the probe** (the committed source of truth — no ad-hoc
console scripts for evidence runs):

- `configs/labgrid/x280-serial-fleet.json` — enrollment + identity contract,
  including the DARK port (kept visible, not silently dropped).
- `configs/labgrid/x280-serial-fleet.yaml` — local-mode labgrid env (no
  coordinator on this bench; the farm's coordinator is not reachable from the
  serial fleet hosts).
- `scripts/bench/serial-fleet-probe.py` — proves console + SSH + identity per
  enrolled unit; non-zero exit if any enrolled unit fails. Run it before and
  after every evidence session.
- `tests/bench/test_serial_fleet_health.py` — the pytest gate over the same
  contract; skips cleanly on hosts without FTDI consoles.

**No power control on this bench.** The USB hubs switch the *adapters*, not the
routers. A power cycle is a manual bench action; tests must not assume one, and
the env deliberately declares no PowerPort.
