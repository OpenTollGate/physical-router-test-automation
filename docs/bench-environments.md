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

## Environment 5 — serial-attached COVR-X1860 (onboarding bench)

**Where:** plugged into a serial fleet host's FTDI USB-TTL adapter, 115200 8N1,
3.3 V. On x280 the COVR console is `/dev/ttyUSB2` (FTDI serial **`BH00D6DL`**) —
`/dev/ttyUSB0` is `tollgate-0195` and `/dev/ttyUSB1` is a COMFAST login, so
**resolve the port by FTDI serial, never by index** (the index drifts on replug).

**Devices:** D-Link **COVR-X1860 A1**, OpenWrt board name `dlink,covr-x1860-a1`,
`mipsel_24kc` / `ramips-mt7621`. Two units are listed as `x1860` in
Environment 1's device set, so expect siblings; pin identity before touching one.

**What runs here:** onboarding and manual-verification runs — flash to vanilla
OpenWrt 25.12.5, install `tollgate-wrt` from the FreedomTechFeed feed, and the
manual happy path (captive portal → pay → gate opens). Not farm-scheduled: it is
not PoE power-controlled, so a human or an agent holds it for a window.

**Access:** serial console (this is the only way in while dropbear is
publickey-only), plus SSH/HTTP over the box's **LAN** side once cabled.

**Mutex — this is the important part.** The COVR is **single-owner hardware**,
one UART, and several context windows reach it. Take the shared bench lock with a
**device-scoped id**, or you will either collide with another window or lock the
wrong board:

```sh
TG_BENCH_ID=tollgate-COVR-X1860 ~/.local/state/tg-bench/tollgate-COVR-X1860.lock
```

Do **not** use the default `tollgate-A04E` id here — that is the MT6000, and
locking it while touching the COVR protects nothing while blocking a legitimate
MT6000 run. Read-only batches take the lock too: reading is traffic, and the
2026-10-14 "console handed away" failure looked exactly like a read-only run that
got someone else's noise. Fail loud, never queue silently.

**Known trap (measured 2026-10-22).** Dropbear on this box came up with
`PasswordAuth 'off'` **and** `RootPasswordAuth 'off'`, carrying an
`authorized_keys` entry that matched no key in our fleet. Serial is the only way
in until password auth is enabled. Also: `192.168.1.1` on the lab LAN is **not**
necessarily this router — a GL.iNet MT6000 answers there (MAC
`94:83:c4:d1:57:58`, OUI `94:83:c4`). Pin identity by MAC before any destructive
step; the COVR's `br-lan` MAC is on its own label.

## The confusion this doc exists to kill

| If a session sees... | It is... | Verdict |
|---|---|---|
| `TollGate-326D` / `192.168.1.1` | Env 2 (laptop bench) | Ours; test via laptop bridge |
| `TollGate-1690` / `TollGate-D1C6` / `100.90.x` | Env 3 (external NetBird) | Out of scope |
| WS3915i / x1860 / PoE control | Env 1 (labgrid farm) | Ours; use the coordinator |
| `dlink,covr-x1860-a1` / `COVR-X1860` / FTDI `BH00D6DL` | Env 5 (serial COVR bench) | Ours; serial console, device-scoped bench lock |
| `tollgate-52BE` / `tollgate-A04E`, `10.57.113.1` / `10.63.101.1`, serial on T440/x280 | Env 4 (serial fleet bench) | Ours; reach on the **management net**, not `192.168.1.1` |
| `192.168.1.1` **from the gateway** | post-install box (Env 4) at a dead address | routes out the WAN — use `ip route get` |
| "MT3000 (arm64)" with no other identity | AMBIGUOUS — must be pinned before use | Identity check mandatory |
