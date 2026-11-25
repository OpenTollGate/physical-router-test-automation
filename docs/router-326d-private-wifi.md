# Router 326D — private WiFi (`c08r4d0r-326D`) credentials + connectivity notes

**Date:** 2026-10-05
**Device:** the Env-2 laptop-bench MT3000 tollgate router, host suffix `326D`
(same box as the live-commission incident of 2026-10-01) — identified by its
box record in `scripts/bench/boxes/`, NOT by an address.
**Access path used (2026-10-05, historical):** SSH `root@192.168.1.1` over
USB-ethernet (`enp0s20f0u4`, NM profile `openwrt-dhcp`, host addr
`192.168.1.127/24`). BatchMode key auth works; no password needed.

**Source of truth — this doc is a dated incident record, not a live
inventory.** Every concrete address, interface name, SSID, and subnet below
is a 2026-10-05 observation kept for the lesson; do NOT copy them as current
values. Current addressing/inventory: the box record +
`scripts/bench/device-identity.sh` (`verify` before anything destructive,
`show` to resolve). Current credentials: the credentials store. Remaining
owner actions from the 2026-10-21 audit of this doc (the credential finding):
(a) confirm/complete the rotation of both keys recorded here (the working
tree was redacted 2026-10-06, commit `acbc531`; rotation of live keys is the
irreversible half and stays an owner step), and (b) the pre-redaction
plaintext still exists in git history (the `5bb1ded` lineage) — a history
purge is an owner-gated rewrite, not a doc edit.

## TL;DR

- SSID `c08r4d0r-326D` (WPA2, both 2.4G radio0 and 5G radio1, same key):
  **`<redacted — rotated 2026-10-06; see credentials store>`** (UCI: `wireless.private_radio{0,1}.key`,
  encryption `psk2+ccmp`).
- SSID `TollGate-326D` is **open** (no password) on both radios.
- Yes, `c08r4d0r-326D` gives internet: it bridges to `br-private`
  (`192.168.2.0/24`), firewall `private` zone forwards to `wan` (which includes
  `wwan`, masq on). Verified live: DHCP lease `192.168.2.206`, HTTPS through
  the interface → HTTP 200.
- Uplink is the router's own STA (`wireless.tollgate_uplink`) on
  **`w3.hub Guests`** (`psk2`, key `<redacted — network retired>`) → `wwan` DHCP
  (10.174.80.x). Router-side ping to 1.1.1.1 OK (~245 ms avg — captive-grade
  upstream, not fast).

## What happened this session (the lesson)

1. Host NM profile `cobrador` (SSID `c08r4d0r-326D`) carried a **stale PSK**
   `c08r4d0r123`. `nmcli connection up` failed twice ("Secrets were required"
   via missing passwd-file, then "base network connection was interrupted")
   — the stored key simply doesn't match the AP.
2. Asserting from the router instead of guessing: `uci show wireless` gave the
   real key (`<redacted — rotated 2026-10-06; see credentials store>`) and the full topology below.
3. `nmcli connection modify "cobrador" 802-11-wireless-security.psk …` + up →
   associated, got `192.168.2.206/24`.
4. Internet proof bound to `wlp59s0`: `curl --interface wlp59s0
   https://www.google.com` → **HTTP 200**. Note: `ping -I wlp59s0 1.1.1.1`
   was 100% loss while TCP worked — on this path, **probe with TCP, not ICMP**
   (see `docs/live-commission-incident-2026-10-01.md` era notes and the
   neverssl/TCP-filter precedents; ICMP through this chain is unreliable).

**Rule:** a locally cached WiFi PSK is a claim, not a fact. Ground truth for
this router is `uci show wireless` over SSH; correct the NM profile from the
router, never the other way around.

## Topology (from `uci show wireless` / `ip route`, 2026-10-05)

| Interface (UCI) | Radios | SSID | Security | Network | Subnet |
|---|---|---|---|---|---|
| `default_radio0` (2G AP), `default_radio1` (5G AP) | radio0/1 | `TollGate-326D` | open | `lan` (br-lan) | 192.168.1.0/24 |
| `private_radio0` (2G AP), `private_radio1` (5G AP) | radio0/1 | `c08r4d0r-326D` | psk2+ccmp, `<redacted — rotated 2026-10-06; see credentials store>` | `private` (br-private) | 192.168.2.0/24 |
| `tollgate_uplink` (STA) | radio0 | `w3.hub Guests` | psk2, `<redacted — network retired>` | `wwan` | 10.174.80.0/24 (DHCP) |

Firewall: zones `lan`→`wan` and `private`→`wan` forwarding, `wan` zone =
`wan wan6 wwan` with `masq=1`. Default route via `phy0-sta0` (10.174.80.2).

`c08r4d0r` = leetspeak "cobrador" — matches the host NM profile name.
