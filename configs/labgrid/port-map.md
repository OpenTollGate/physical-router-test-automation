# Rig Port Map — GS1900-8HP #1 (OpenWrt), live discovery 2026-09-25 (rev 2)

Rev 2: corrected identities + credentials from the conwrt handover
(data/bench/places.json, inventory.jsonl), the wedge incident + recovery,
and the serial-routing model. Discovery method: `ssh root@192.168.13.2`
(read-only): `ubus call poe info`, `/etc/config/network`, `ip neigh`, SSH
probes of each DUT via `-J root@192.168.13.2`. Precedent:
`conwrt/recipes/extreme-networks/ws-ap3915i/HARDWARE-DISCOVERY.md`.

## Switch identity

| Field | Value |
|---|---|
| Model | Zyxel GS1900-8HP **A1** (RTL8380M rev C), PoE MCU ST32F100 fw v17.1 (BCM59121 PSE) |
| Firmware | OpenWrt **25.12.1** r32768-b21cfa8f8c, kernel 6.12.74, target `realtek/rtl838x`, Amperstrand realtek-poe fork |
| Mgmt IP | **192.168.13.2** (switch.1 / VLAN 1, gw 192.168.13.1 = ERX house core) |
| PoE budget | 65 W (typ. consumption 21-22 W fully populated) |
| Port naming | `lanN` = phys port `p(7+N)`; per-DUT VLAN `100N` on lanN, switch = gateway `192.168.10N.1`, DUTs at `.51` |
| Known issues | realtek-poe daemon↔MCU wedge (2026-09-25 incident, see below; 25.12.5 upgrade = the planned fix, plan W2). Switch clock drifts ~27 min (NTP unsynced) — matters for log correlation. The September "self-reboots" were the user's bench power cuts, not a fault. |

## Port map

| Port | VLAN | PoE | DUT | Firmware / state | Mgmt + auth | Notes |
|---|---|---|---|---|---|---|
| **lan1** | 1 + 1002-1008 trunk | admin Disabled | — | Lab-LAN uplink | — | **PROTECTED** (uplink + DUT trunk) |
| **lan2** | 1002 | 5.2 W | **WS-AP3915i** (dc:b8:08:6c:ea:7f) | OpenWrt **25.12.5**, clean overlay | 192.168.102.51, root, pw <secrets:fleet.bench_root_password> (canonical: ~/conwrt-bench) **and** our key (5 keys installed) | **REFERENCE UNIT + serial-listener infrastructure** — held acquired (busy) in labgrid, NOT a DUT. Overlay: no reflash/firstboot until the switch runs 25.12.5. Serial bridge: ai-legion `conwrt_serial_bridge.py` :4003 |
| **lan3** | 1003 | 5.0 W | **WS-AP3915i** (b4:2d:56:25:47:a2) | OpenWrt **24.10.2** | 192.168.103.51, root, **key-only by design** (password auth disabled 2026-09-23) | Serial splice CURRENTLY ATTACHED here (listener ap-lan2 → console lan3). Default serial-test DUT per owner's routing model |
| **lan4** | 1004 | 3.4 W | **WS-AP3915i** (b4:2d:56:25:79:b1) | **DARK since 2026-09-24 ~11:03** (was the healthy reference unit; went dark after the switch-reboot PoE cycle; link + ~3.4 W, zero frames since) | none | Serial-gated recovery (plan W1). Was the serial listener for ap-lan2's own console (:4002, now removed). NOT the May-era boot-failure unit |
| **lan5** | 1005 | 4.9 W | **WS-AP3915i** (b4:2d:56:24:ad:97) | OpenWrt **24.10.2**, flash-boots post-#61 (CFG1 env-identity fix) | 192.168.105.51, root, **our key** (key auth proven) | **PRTA rig smoke-test DUT** (`router-alpha`). Survived: 3 power cycles + the 2026-09-25 wedge-dark episode (recovered via daemon restart) |
| **lan6** | 1006 | 3.5 W | never identified (no MAC ever learned) | **DARK, zero frames EVER** incl. bootloader; "boots STOCK" (#79: boot_net env repair = plan W1) | none | Serial-gated ("zero frames ever" class). `reset_allowed=true` but do NOT lottery-cycle |
| **lan7** | 1007 | Searching | — | empty | — | spare |
| **lan8** | 1008 | Other fault (expected: non-PD link partner) | — | **cascade to GS1900-8HP #2 (stock V2.90)** | — | **PROTECTED**. Stock #2's old mgmt 192.168.1.1 is DEAD; now on the lab LAN at an unknown 192.168.13.x address (herdr agent `stock-poe` finding it; report → ~/stock-2.90-investigation.md) |

## Serial-routing model (owner directive)

ap-lan2 (reference unit) is **dedicated labgrid infrastructure**: its
/dev/ttyMSM0 listens on the RJ45 null-modem splice, wired to whichever DUT
needs serial. **Default splice position: lan3.** Moves are manual (owner's
hands) + `scripts/bench_serial_route.py <place>` on ai-legion flips the
bridge instance + exporter stanza + restart in one shot. Exactly ONE
NetworkSerialPort stanza active in the exporter at a time.

## labgrid topology (post-consolidation)

- **Canonical coordinator: 192.168.13.208:20408 (ai-legion)** — conwrt bench
  places `ap-lan2..8` (live power/serial exports) + embedded-family places
  (migrated from 221). Power backend convergence:
  labgrid/POE-BACKEND-DECISION.md (conwrt repo). Adoption sketch:
  ~/src/physical-router-test-automation/docs/labgrid-rig-exporter-adoption.md.
- 192.168.13.221:20408 (small) — retired during consolidation; its stale
  ap-lan2..8 duplicates deleted.
- Local (no-coordinator) env for the PRTA smoke lane:
  `configs/labgrid/rig-alpha.yaml` + `tools: ssh:` ProxyJump wrapper.

## Incident log: 2026-09-25 PoE daemon wedge

- Driver-detected: `poe manage` silently dropped (rc=0) while `poe info`
  served a byte-identical frozen snapshot; no daemon log lines. Earlier
  tell: 07:12 logread "MCU rejected command: not-ready".
- Effect: lan5 port state stale → its AP went network-dark ~10:29.
- Recovery (user-approved, bench rule): release places → `/tmp/amperstrand-bench`
  flock → `/etc/init.d/poe restart` → control verified. Observed: healthy
  PDs did NOT blip (lan2/lan3 uptimes continuous); only the wedged lan5
  renegotiated → dark AP recovered.
- Hardening now in `ZyxelPoEDriver` (tollgate-lab): manage + poll-verify,
  settling tolerance, frozen-digest DROPPED detection, bounded retry for
  the not-ready transient class.

## PROTECTED (never PoE-toggle, never reboot switch)

**lan1** (uplink + trunk), **lan8** (cascade to stock #2), the switch itself
(a switch reboot cold-cycles every PD — documented in conwrt recipes).
