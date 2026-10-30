# MeshLink ↔ TollGate Interoperability Plan

Status: seller lane passing; buyer core tested, Beta chain pending
Owner: PRTA / TollGate lab
Last updated: 2026-10-30

## Goal

Make a MeshLink fork speak the TollGate protocol in both directions:

1. **Sell:** MeshLink acts as a TollGate merchant. A TollGate client/reseller
   discovers its advertisement and pays it with a Cashu bearer token.
2. **Buy:** MeshLink acts as a TollGate customer/reseller. It discovers an
   upstream TollGate, pays it with Cashu, tracks usage, and renews.

The work is proven in the existing QEMU lab and exposed through labgrid, then
accepted against the virtual Beta seller first and a physical labgrid place
second.

## Decisions

| Decision | Choice |
|---|---|
| Scope | Both roles, seller first |
| Base OS | Debian 12 lab image (fast bring-up; Debian 13 lane later) |
| Buyer implementation | TypeScript Cashu wallet in the broker |
| Seller for acceptance | Virtual Beta OpenWrt module (physical place second) |
| Protocol surface | TIP-01, TIP-02, HTTP-01, HTTP-02, HTTP-03 |
| Wire port | `2121` (TollGate default), separate from broker `:3000` |
| Pricing | Dedicated `tollgate` block, not derived from Stripe tiers |

## Repositories and branches

| Path | Branch | Upstream |
|---|---|---|
| `~/repos/meshlink-wifi-gateway` | `labgrid-tollgate-interop` | `MeshLinkFoundation/meshlink-wifi-gateway` |
| `~/repos/meshlink-broker` | `labgrid-tollgate-interop` | `MeshLinkFoundation/broker` |
| `~/worktrees/prta-meshlink-interop` | `meshlink-interop-lab` | `OpenTollGate/physical-router-test-automation` |

## Target topology

```
host 10.99.98.2 (CDK FakeWallet :8383, NAT internet)
 └ tg-upstream-br 10.99.98.0/24
    ├ beta/seller OpenWrt  (WAN 10.99.98.1, LAN 10.99.96.11)
    └ meshlink WAN         (DHCP 10.99.98.x, gateway .1)

tg-mesh-br 10.99.100.0/24
 ├ meshlink guest 10.99.100.1 (dnsmasq, ipset, broker, :2121)
 └ client 10.99.100.100 (Debian or netns; runs tollgate-agent)

mgmt-br 10.99.97.0/24
 ├ meshlink mgmt 10.99.97.15 (SSH, labgrid target)
 └ client mgmt 10.99.97.16
```

Canonical mint URL for the lane: `http://10.99.98.2:8383`.

A guest must be able to reach that mint **before** the gate opens. MeshLink
therefore needs a pre-auth FORWARD accept for the mint host/port, matching the
TollGate module's behavior.

## Phase 1 — QEMU lane

- [x] Add `prta/scripts/meshlink-lab.py` (`prepare`, `start`, `stop`, `status`, `provision`, `smoke`, `buy`).
- [x] Reuse the Debian 12 base and NoCloud seed flow.
- [x] Start MeshLink with three virtio NICs: wan0, guest0, mgmt0.
- [x] Provision Node 20 and the fork checkout; lab uses a prebuilt broker dist to avoid a 2 GB Vite OOM.
- [x] Add `prta/configs/labgrid/meshlink-lab.yaml` with MeshLink gateway and client targets.
- [x] Add Make targets: `meshlink-lab-up`, `meshlink-lab-down`, `meshlink-lab-status`, `meshlink-interop`.
- [x] Smoke: broker `:3000/api/health`, TollGate `:2121/`, client can reach the mint.

Acceptance: the lane comes up from a clean checkout and `curl` checks pass.

## Phase 2 — MeshLink as seller

### Gateway fork

- [x] Add `install.sh --lab-qemu --guest-interface guest0 --wan-interface wan0 --ap-ip 10.99.100.1`.
- [x] Skip hostapd, rfkill, WiFi country, NetworkManager AP unmanage, HaLow unit.
- [x] Create `br-guest` from the wired guest NIC; assign `10.99.100.1/24`.
- [x] dnsmasq listens on `br-guest`; the broker sees `MESHLINK_AP_INTERFACE=guest0`.
- [x] INPUT allows `:2121`; FORWARD allows mint host/port pre-auth.
- [x] Broker service keeps after `meshlink-network` and `dnsmasq`.

### Broker fork

- [x] `server/tollgate/identity.ts`: persisted Nostr key, 0600.
- [x] `server/tollgate/advertisement.ts`: signed `kind 10021`.
- [x] `server/tollgate/cashu.ts`: `@cashu/cashu-ts` receive/swap wallet.
- [x] `server/tollgate/store.ts`: `tollgate.db` session + redemption + proof store.
- [x] `server/tollgate/routes.ts`: second Express listener on `:2121`.
- [x] `GET /` advertisement, `GET /whoami`, `GET /usage`, `POST /` payment.
- [x] Signed `kind 1022` session response; `kind 21023` notices on errors.
- [x] Derive MAC from neighbor/ARP; never trust a client-supplied MAC.
- [x] Unique token hash, rate limit, no token logging; swap then grant.
- [x] Config block: `tollgate.enabled`, `metric`, `stepSize`, `pricePerStep`, `unit`, `mintUrl`, `minSteps`, `maxSteps`.

Acceptance:

- [x] Ad is valid JSON, `kind 10021`, signed, has `metric`, `step_size`, `tips`, `price_per_step`.
- [x] `tollgate-agent` buys from MeshLink, exits 0.
- [x] Client reaches the internet after payment.
- [x] `GET /usage` advances.
- [x] Replay and wrong-mint payments return `21023` and grant nothing.

## Phase 3 — MeshLink as buyer

- [x] `server/tollgate/upstream.ts`: discover ad on WAN gateway `:2121`.
- [x] Strict ad parsing and unit/price caps.
- [x] Budget caps: max price/step, max sats/minute, daily budget, min steps.
- [x] Persisted Cashu wallet proofs; optional FakeWallet auto-mint for the lab.
- [x] Classify `200/1022`, `400/21023` codes, and `5xx`/timeout as ambiguous.
- [x] NUT-07 reconciliation; never blind-retry an ambiguous payment.
- [x] Persist spend totals and proofs; never log tokens.
- [ ] Poll `/usage`, renew near expiry (renewal timer exists, usage polling pending).
- [x] Expose status at `/api/meshlink/upstream`.

Acceptance:

- [x] MeshLink discovers Beta and pays it (`granted`, 1 sat).
- [x] Beta `/usage` advances (`9216/154140672` after portal traffic).
- [ ] MeshLink guest reaches the internet through the paid upstream.
  Blocked by the Beta VM acting as a one-armed gateway: its nodogsplash
  `ndsAUT`/forward path rejects the double-NAT return path even with a paid
  host session. Payment and usage are proven; traffic steering is next.
- [ ] One renewal completes (usage-poll renewal implemented; Beta allotment
  is too large to hit 80% without a working traffic path).
- [x] Price above cap causes no purchase (unit-tested in the buyer core).

## Phase 4 — chain, labgrid, docs

- [ ] Chain test: host → Beta → MeshLink → client.
- [ ] Physical labgrid place mode (`--seller-place`).
- [ ] `tests/interop/` suite with story/TIP markers.
- [ ] `docs/meshlink-tollgate-interop.md` kept current.
- [ ] Update `docs/virtual-lab.md`.

## Test matrix

| Test | Proves |
|---|---|
| Ad shape + signature | TIP-01/02 discovery |
| `tollgate-agent` buys | MeshLink sells to a TollGate client |
| `/usage` advances | session/allotment semantics |
| Replay / wrong mint | payment safety |
| MeshLink pays Beta | MeshLink buys from a TollGate |
| Renewal | session lifecycle |
| Chain end-to-end | full reseller interoperability |
| Physical place | fleet compatibility |

## Risks

- MeshLink gates by IP; TollGate gates by MAC. Return MAC now; add
  `ipset hash:mac` if a real client requires it.
- One `step_size` + integer sat price cannot express Stripe tiers. Use a
  dedicated `tollgate` pricing block.
- Debian 12 is a speed choice; add a Debian 13 lane if upstream diverges.
- No WiFi/beacon emulation; wired guest bridge only.
- Verify `@cashu/cashu-ts` against the lab CDK FakeWallet
  (NUT-00/01/02/03/04/07).
- MeshLink gateway repo has no license file; resolve before distributing a fork.

## Lab runbook

```bash
make meshlink-lab-prepare
make meshlink-lab-up
make meshlink-lab-provision
make meshlink-interop      # smoke + agent purchase + replay/wrong-mint
make meshlink-lab-buyer
/home/c08r4d0r/prta/.venv/bin/python -m pytest \
  --lg-env=configs/labgrid/meshlink-lab.yaml tests/interop -q
make meshlink-lab-down
```

The lane uses `ml-mgmt-br` (not `mgmt-br`) because Linux interface names are
capped at 15 characters. The client gets `10.99.100.47` from MeshLink DHCP and
is reached by labgrid over `10.99.97.16`.

Known lab constraints:

- The host firewall needs `INPUT -i tg-upstream-br --dport 8383` for the mint.
  `scripts/meshlink-lab.py` installs this idempotently.
- The QEMU broker build OOMs in a 2 GB guest. The lab copies the host's
  `dist/` and runs `npm ci --omit=dev` with `MESHLINK_LAB_PREBUILT=1`.
- The host `tollgate-agent` binary is glibc 2.39; Debian 12 needs the
  `x86_64-unknown-linux-musl` build. The lab cloud-init prefers it.
- 9p sharing uses mount tag `hostrepos`; if it is not mounted automatically,
  rerun `make meshlink-lab-provision` (it mounts idempotently).

## Buyer configuration

```json
"upstream": {
  "enabled": true,
  "url": "http://10.99.98.1:2121",
  "unit": "sat",
  "steps": 1,
  "maxPricePerStep": 10,
  "maxCostPerMinute": 2,
  "dailyBudget": 100,
  "renewAtPercent": 80,
  "autoMint": false,
  "pollIntervalMs": 60
}
```

`autoMint` relies on a FakeWallet mint that settles its own invoices; it is for
the QEMU lane only. A production buyer must be funded with proofs in
`upstream-proofs.json` (mode 0600) or learn to pay a real Lightning invoice.

Status: `GET /api/meshlink/upstream` on the broker (`:3000`).

## Progress log

- 2026-10-30: plan written; forks created on `labgrid-tollgate-interop`;
  PRTA worktree created on `meshlink-interop-lab`.
- 2026-10-30: QEMU lane boots Debian 12 gateway + client on three bridges with
  a host FakeWallet mint. MeshLink advert is served on `:2121`.
- 2026-10-30: `tollgate-agent` (musl build) paid MeshLink 1 sat; seller
  granted a 12,600,000 ms session, `/usage` advanced, and the client reached
  `https://example.com`. `tests/interop/test_meshlink_seller.py`: 3 passed.
- 2026-10-30: replay and wrong-mint payments are rejected with
  `payment-error-token-spent` and `payment-error-invalid-token` (`21023`, 400).
- 2026-10-30: buyer core (`server/tollgate/upstream.ts`) mints/spends with
  cashu-ts, parses the advert, applies caps, reconciles with NUT-07, and
  granted against a mock kind:10021 seller. `make meshlink-lab-buyer` reports
  `{"state":"granted","allotment":60000,...}` from the live broker.
- 2026-10-30: MeshLink paid the virtual Beta OpenWrt seller at
  `10.99.99.1:2121` (bytes metric, 1 sat) and Beta `/usage` advanced. Guest
  traffic through Beta still fails on Beta's one-armed nodogsplash return path.
- 2026-10-30: buyer renewal now polls `/usage` and only repurchases at
  `renewAtPercent`; millisecond grants keep the time-based fallback. Installer
  verification retries listeners for up to 30s instead of false-failing.
