# PoE Venue Roadmap — physical lab on the GS1900 bench switch

State snapshot: 2026-09-22. Owner: PRTA (test scenarios) + conwrt (bench
rig) + tollgate-lab (shared primitives). Read together with
`conwrt/data/sessions/2026-09-22-SESSION-CLOSEOUT.md` (bench portrait)
and `conwrt/docs/BENCH-SWITCH-PATTERN.md` (rig).

## Bench portrait (as of 2026-09-22)

| Port | Unit | Firmware | Power-cycle policy |
|---|---|---|---|
| lan1 | ERX uplink trunk (VLANs 1002–1008) | 25.12.4 | **PROTECTED** (not a DUT) |
| lan2 | AP3915i UNIT2 review | 25.12.5-era, self-booting | OK |
| lan3 | AP3915i recovered | 24.10.2, TFTP-dependent | **PROTECTED** until #61 |
| lan4 | AP3915i reference | 24.10.2, self-booting | OK |
| lan5 | AP3915i fallback-dependent | — | **PROTECTED — ONE-WAY TRIP** until TFTP lifeline re-armed |
| lan6–8 | empty (lan6 dark/serial-gated #62) | — | n/a |

Enforcement: `configs/labgrid/inventory.local.yaml` `protected_ports` +
per-router `protected: true` → `PoeControllerConfig.protected_ports` →
`PoePowerController._manage()` refuses before any ubus call. lan1/lan3/lan5
are currently protected. **Ask conwrt to not export a power place for
lan5** — the labgrid path bypasses our controller.

## Updating the inventory

The inventory (`configs/labgrid/inventory.local.yaml`, gitignored) is
PRTA's local copy of bench truth. Update procedure — sources in order:

1. Newest `conwrt/data/sessions/*CLOSEOUT*` / `*bench-state*` doc
   (port map, unit lineage, hazards).
2. Live switch, read-only: `ssh root@<switch> 'ubus call poe info'` —
   validates which ports actually Deliver.
3. Address discovery after power events (DHCP leases / ARP), when the
   bench is free.
4. Edit the file; validate:
   `python3 -c "import sys; sys.path.insert(0,'.'); from lib.lab_inventory import load_inventory; i=load_inventory(); print(sorted(i.routers), sorted(i.protected_ports))"`
5. Committed schema/example carries placeholders only — MACs, serials,
   passwords, per-unit IPs never enter git.

Rule: never power-toggle a port to "discover" a device while another
session works; never infer the map from a single source.

## Phase 1 — direct SSH power control (DONE, pending live re-verify)

- `tollgate_lab/hardware/poe.py` — verified-manage controller: action-form
  ubus (`poe manage {"port","action"}` + `set_port_config` fallback),
  state verification by polling, min-off-time 8s, budget projection,
  frozen-daemon detection (`PoeUnresponsiveError`), protected-port
  refusal (`PoeProtectedPortError`). 27 unit tests.
- PRTA entry: `TOLLGATE_POE_SMOKE=1 pytest tests/scenarios/test_poe_power_cycle.py --no-deploy`
  (gate router via `TOLLGATE_POE_GATE`, default `ap-lan2`).
- Recovery orchestration: `scripts/recovery/switch_tftp_recovery.py`
  (dry-run default; `--execute` gated; re-inserts the runtime-only nft
  rule its serve-step needs after switch reboots).

## Phase 2 — labgrid coordinator client (NEXT)

conwrt already runs the topology: coordinator at the shared rig address
(see inventory `coordinator.address`), exporter on the lab host,
`conwrt_poe` power backend, places `ap-lan3..8`. PRTA becomes a client:

1. Smoke as a client first:
   `labgrid-client -x <coordinator> places` then acquire/power/release on
   an agreed place.
2. Replace the inline env in `configs/labgrid/physical-poe-lab.yaml.example`
   with `RemotePlace: {name: ap-lan2}` per target; keep the Phase-1
   direct controller as fallback for bench bring-up.
3. Converge semantics: conwrt's `conwrt_poe` backend does fire-and-forget
   manage; our controller verifies + detects frozen daemons. Contribution
   candidate: port the verify/frozen logic into the backend (or their
   fork's enable-bool patch + uhttpd-mod-ubus → stock ubus backend, per
   their own hardening note).
4. Locking discipline unchanged: BenchLock FIRST, then labgrid place
   acquire, then RouterLock. Labgrid places exclude only labgrid-aware
   callers.
5. labgrid YAML gotcha (from conwrt): comments must use `##`, never `#`
   (Jinja templates).

## Release testing on the PoE venue (target: next tollgate release)

The AP3915i fleet upgrades the venue vs the shelved NR7101: ARM
Cortex-A7 (fully covered by tmbg CI matrix) AND dual-band WiFi — the
portal/phone tier becomes possible on PoE-controlled hardware, plus
25.12.x = APK packaging path on lan2's UNIT.

Allocation is pending the operator's WiFi-mesh plan (lan4 = gateway
candidate) — coordinate before squatting. Proposed split when agreed:

- `ap-lan2` (25.12.5, self-booting): tollgate release SUT — deploy RC,
  API tier, token formats, cold-boot persistence, power-churn soak.
- `ap-lan4` (24.10.2 reference): operator mesh gateway; PRTA uses only
  with explicit handoff.
- Portal tier (Playwright/phone): whichever unit the operator assigns;
  fallback remains GL-MT3000 alpha.
- Publish gating unchanged: physical venue never publishes; public
  evidence via a parallel SHC cloud run of the same artifact.

## Open items

1. Verify `manage` responsiveness on the rebuilt switch (toggle an EMPTY
   port, never lan3/lan5) — no one has power-managed since the redeploy.
2. conwrt: drop/annotate the `ap-lan5` power place (one-way trip).
3. Addresses: DUTs sit on old statics until `conwrt configure` adopts the
   bench addressing (VLAN 100N, 192.168.10N.50-150) — fill inventory as
   they land.
4. Switch daemon health monitoring: the frozen-detector should run as a
   cheap preflight in every PoE scenario, not just on failure paths.
