# MeshLink ↔ TollGate Physical Chain PR Plan

Status: complete
Owner: MeshLink interop lab
Last updated: 2026-11-03

## Goal

Re-run the full physical chain, capture fresh evidence, and surface the work as
draft PRs with a reproducible test report.

## Chosen strategy

Open three linked draft PRs. The physical test report lives in the broker PR
body; the gateway and PRTA PRs link back to it.

| Repo | Head branch | Base | Scope |
|---|---|---|---|
| `MeshLinkFoundation/broker` | `felixfelix-bot:labgrid-tollgate-interop` | `main` | TollGate merchant, Cashu buyer, paid upstream route steering |
| `MeshLinkFoundation/meshlink-wifi-gateway` | `felixfelix-bot:labgrid-tollgate-interop` | `main` | QEMU lab mode, TollGate seller listener, DNS override |
| `OpenTollGate/physical-router-test-automation` | `felixfelix-bot:meshlink-interop-pr` | `main` | MeshLink interop lab harness, pytest lane, docs, physical runbook |

## Draft PR links

- Broker: https://github.com/meshlinkfoundation/broker/pull/1
- Gateway: https://github.com/meshlinkfoundation/meshlink-wifi-gateway/pull/3
- PRTA: https://github.com/OpenTollGate/physical-router-test-automation/pull/336

## Diff size

Focused PR diffs against current upstream `main`:

| Target | Files | Diff |
|---|---:|---:|
| Broker | 14 | +1520 / −21 |
| Gateway | 2 | +218 / −48 |
| PRTA | 8 | +1958 / −0 |
| Combined | 24 | +3696 / −69 |

The PRTA branch should be cut from upstream `main` and contain only the six
MeshLink interop commits plus the PR plan commit:

- `83f736e` Add MeshLink TollGate interop lab
- `68d5c1f` meshlink: prove tollgate chain traffic and renewal
- `6c072f1` meshlink: add physical seller chain mode and TIP markers
- `b105d42` meshlink: fix physical seller CLI wiring
- `d306331` meshlink: guard off-link physical sellers
- `74eb182` meshlink: document physical seller chain via private bridge
- `c2f5ba9` meshlink: add physical chain PR plan and evidence

Do not include the unrelated E750/bench commits from the full fork `main`.

## Manual physical run

Seller: labgrid place `gl-mt3000`, on-link private bridge `192.168.2.1:2121`.
The WAN address `192.168.50.205` is off-link and is not used for the chain.

```bash
labgrid-client -x x280.fips:20408 -p gl-mt3000 acquire
# Temporarily prepend http://192.168.2.201:8383 to accepted_mints and restart.
curl -s http://192.168.2.1:2051/splash.html >/dev/null
labgrid-client -x x280.fips:20408 -p gl-mt3000 ssh \
  'ndsctl clients; ndsctl auth <token>'
MESHLINK_SELLER_URL=http://192.168.2.1:2121 \
MESHLINK_SELLER_PLACE=gl-mt3000 \
  make meshlink-lab-chain-physical | tee /tmp/meshlink-physical-chain-2026-11-03.log
```

Temporary lab-only changes:

- Seller accepted-mint list gets the lab FakeWallet mint; restore the saved
  config afterwards.
- Broker `upstream` points at the physical seller with `routeThrough: true`;
  disable it and remove routes after the run.
- Sell session is paid with lab fake ecash, not real funds.

## Fresh physical evidence (2026-11-03)

Captured to `/tmp/meshlink-physical-chain-2026-11-03.log`:

```text
{"state":"granted","allotment":60000,"metric":"milliseconds","deviceIdentifier":"mac","gateway":"http://192.168.2.1:2121","cost":1,"route":"installed","purchases":1,"lastGrantAt":"2026-11-02T23:31:14.512Z"}
PROBE gate=http://10.99.100.1:2121/ offers=1 decision=Buy { gateway_id: "http://10.99.100.1:2121/", steps: 1, cost: 1.0, unit: "sat" }
RESULT key=c294813bbc13cfe2 state=granted cost=1 outcome=Granted { allotment: Some(12600000), metric: Some("milliseconds"), device_identifier: Some("mac") }
usage: 220/12600000
internet: ok
seller usage before: 11000/60000
traffic 200 50000000   # x5
seller usage after: 34000/180000
{"state":"granted","allotment":180000,"metric":"milliseconds","deviceIdentifier":"mac","gateway":"http://192.168.2.1:2121","cost":1,"route":"installed","purchases":3,"lastGrantAt":"2026-11-02T23:32:45.312Z"}
```

Result: MeshLink paid the physical seller 1 sat, five 50 MB downloads
succeeded through the guest client, seller `/usage` advanced, and automatic
renewal reached `purchases: 3` with the paid route installed.

## Draft PR bodies

Broker PR body should contain:

- Summary of merchant + buyer + route steering.
- Linked gateway and PRTA draft PRs.
- Test matrix: seller advert, `tollgate-agent` purchase, replay/wrong mint,
  virtual Beta chain, physical `gl-mt3000` chain.
- Fresh physical evidence block and reproduction commands.
- Lab limitations: fake-mint funding, private-bridge seller topology, no
  real seller config left modified.
- Note any pre-existing unrelated `tsc` failures.

Gateway PR body: QEMU lab mode, seller listener, DNS override; linked broker PR.

PRTA PR body: harness/docs/physical runbook; linked broker PR.

## Checklist

### Documentation

- [x] Write this plan and checklist.

### Manual physical test

- [x] Acquire `gl-mt3000`.
- [x] Temporarily expose lab fake mint on the seller.
- [x] Register and authenticate the broker source MAC in NDS.
- [x] Run the full physical chain and capture the log.
- [x] Record grant, traffic, seller usage, and renewal evidence here.
- [x] Restore seller config and release the place.
- [x] Disable broker upstream and remove host routes/rules.

### PR branches

- [x] Verify pushed broker branch and diff against upstream `main`.
- [x] Verify pushed gateway branch and diff against upstream `main`.
- [x] Create clean PRTA branch `meshlink-interop-pr` from upstream `main`.
- [x] Cherry-pick only the MeshLink commits plus plan onto the clean branch.
- [x] Push `meshlink-interop-pr` to the fork.

### Draft PRs

- [x] Open draft PR: broker.
- [x] Open draft PR: gateway.
- [x] Open draft PR: PRTA.
- [x] Add fresh physical evidence to the broker PR body.
- [x] Cross-link all three draft PRs.

### Final verification

- [x] All three PRs are draft.
- [x] PR file counts/diff sizes match this plan.
- [x] Lab restored and all temporary routes/rules removed.
- [x] Report PR URLs and final diff sizes.
