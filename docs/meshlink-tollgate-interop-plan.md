# MeshLink TollGate Interop — Remaining Work Plan

Status: full QEMU chain passes, including traffic through the paid Beta session and automatic renewal.
Owner: MeshLink interop lab.

## Goal

Prove the full reseller path in the local lab:

```text
host / internet
   |
Beta OpenWrt TollGate seller (10.99.99.1:2121)
   |
MeshLink gateway (10.99.98.10, buys from Beta)
   |  br-guest 10.99.100.1
MeshLink client (10.99.100.x)
```

MeshLink must discover Beta, pay it with Cashu, route guest traffic through
the paid session, keep its own management/payment traffic on the direct path,
renew before expiry, and prove Beta usage moved.

## Already proven

- [x] MeshLink advert: signed `kind:10021`, correct tags.
- [x] `tollgate-agent` buys from MeshLink and receives `kind:1022`.
- [x] `/usage` advances and the client reaches the internet after payment.
- [x] Replay and wrong-mint payments return `21023` and grant nothing.
- [x] MeshLink buyer discovers and pays the Beta seller.
- [x] Buyer caps, NUT-07 reconciliation, persisted proofs/spend.
- [x] Buyer `/usage` polling code is implemented.
- [x] Installer verification retries listeners instead of false-failing.
- [x] Lab script has prepare/start/stop/status/provision/smoke/buy/replay/buyer.

## Remaining checklist

### 1. Lab lifecycle and recovery

- [x] Add Beta seller QEMU lifecycle to `scripts/meshlink-lab.py`.
- [x] Add Beta mint `10.99.99.2:3338` lifecycle.
- [x] Add host DNS proxy on `10.99.98.1:53`.
- [x] Include Beta/mint/DNS in `start`, `stop`, and `status`.
- [x] Add `--with-beta` (or explicit beta subcommands) so the seller is optional.
- [x] Make `stop` idempotently remove netns, SNAT, policy routes, DNS, and mints.
- [x] Restore the lab and rerun smoke/buy/replay/pytest.

### 2. DNS

- [x] Add a lab DNS override to `install.sh` (`--dns-server` or lab default).
- [x] Point gateway dnsmasq at `10.99.98.1` instead of `8.8.8.8/8.8.4.4`.
- [x] Ensure policy routing does not capture dnsmasq's own upstream traffic.
- [x] Verify client DNS and HTTPS after a paid session.

### 3. Upstream traffic steering

- [x] Add upstream config: `routeThrough`, `routeTable`, `routeNexthop`, `guestInterface`.
- [x] Add `server/tollgate/upstream-route.ts` (seller routes remain in `routes.ts`).
- [x] Mark only forwarded guest traffic (fwmark), not broker/management traffic.
- [x] Install a dedicated default route to the paid upstream after a grant.
- [x] Keep the upstream next hop reachable through the existing gateway (`routeNexthop`).
- [x] Clean stale routes on broker startup.
- [x] Expose route state in `/api/meshlink/upstream`.
- [x] Verify guest traffic leaves through the paid upstream.

### 4. Beta chain

- [x] Create a paid Beta client in a network namespace (`10.99.99.3`).
- [x] Register/pay that client through Beta's portal and `:2121`.
- [x] SNAT MeshLink traffic to the paid client (host bridge takes the paid MAC after the netns is removed).
- [x] Verify return-path de-SNAT and guest internet.
- [x] Verify Beta `/usage` advances from MeshLink guest traffic.
- [x] Add a repeatable `chain` lab command and Make target.

### 5. Renewal

- [x] Drive usage above `renewAtPercent`.
- [x] Verify the buyer polls `/usage`.
- [x] Verify a second payment and grant.
- [x] Verify the new allotment is reflected in status.

### 6. Tests and documentation

- [x] Correct the stale "usage polling pending" checkbox.
- [x] Add chain/renewal checks to `tests/interop` or the lab script.
- [x] Update `docs/virtual-lab.md` with the Beta chain lane.
- [x] Add story/TIP markers to interop tests.
- [x] Add physical labgrid place mode (`chain --seller-place` with `MESHLINK_SELLER_URL`).
  Full `gl-mt3000` chain passes via its on-link private bridge
  `192.168.2.1:2121`; off-link seller URLs fail before route changes.
- [x] Rerun the full interop suite.
- [x] Commit and push gateway, broker, and PRTA branches.

## Implementation notes

### Upstream route steering

Use a dedicated routing table and mark only forwarded guest traffic:

```text
iptables -t mangle -A PREROUTING -i br-guest -j MARK --set-xmark 0x2121/0xffffffff
ip rule add fwmark 0x2121 lookup 2121 pref 1000
ip route replace table 2121 default via <upstream-nexthop> dev <wan>
```

`routeNexthop` is the on-link host that forwards to the off-link upstream
(`10.99.98.1` in the lab). It avoids the invalid `onlink` gateway case where
the kernel ARPs for the upstream seller on the wrong L2 segment.
The main table keeps the direct default route for broker and management
traffic. The fwmark is removed when the upstream route is removed.

### Beta paid client

Beta is a one-armed OpenWrt gateway using NoDogSplash. It tracks the host at
`10.99.99.2`, so MeshLink traffic sourced from `10.99.98.10` is not treated as
a paid client. The lab puts a dedicated paid client in a netns:

```text
netns ml-beta-client: 10.99.99.3/24, MAC 02:00:00:00:99:03, default via 10.99.99.1
host veth enslaved to tg-poc-br
host SNAT: 10.99.98.0/24 -> 10.99.99.3 when leaving tg-poc-br
host tg-poc-br takes MAC 02:00:00:00:99:03 after the netns is removed
Beta POSTROUTING: 10.99.99.3 -> 10.99.99.1 when leaving br-lan
```

The netns visits the portal and pays Beta. The netns is then removed and the
host bridge takes the paid MAC, so Beta maps SNATed MeshLink traffic and
`/usage` polling to the paid NoDogSplash session. Host conntrack de-SNATs
return traffic back to MeshLink. Beta NAT keeps the return path off the direct
L2 netns path that would otherwise answer with RST. This exercises the real
Beta payment/session path rather than bypassing NoDogSplash.

### Lab DNS

`8.8.8.8/8.8.4.4` are not reachable from the lab gateway. Run a host-side
dnsmasq bound to `10.99.98.1:53` forwarding to the host resolver, and point the
gateway dnsmasq at it. This keeps the lab deterministic and does not change
production defaults.

## Runbook after implementation

```bash
make meshlink-lab-prepare
make meshlink-lab-up          # includes Beta/mint/DNS when enabled
make meshlink-lab-provision
make meshlink-interop
make meshlink-lab-buyer
make meshlink-lab-chain
make meshlink-lab-down
```

## Acceptance

- [x] Full chain: host -> Beta -> MeshLink -> client.
- [x] Beta paid session is used by MeshLink guest traffic.
- [x] Beta `/usage` shows MeshLink traffic.
- [x] One renewal completes with a second payment.
- [x] Seller, replay, wrong-mint, buyer, and chain tests all pass (3 pytest interop tests pass; `make meshlink-interop` and `meshlink-lab-chain` pass).
