# Two-Router Cloud Test Architecture

## Overview

The two-router cloud tests verify TollGate's upstream payment flow in the GCP cloud lab using two OpenWrt QEMU VMs connected via dedicated network bridges. No physical hardware is required.

## Topology

```
┌─────────────────────────────────────────────────────────────────┐
│                    GCP VM (nested KVM)                          │
│                                                                 │
│  ┌──────────────────┐  tg-poc-br  ┌──────────────────┐        │
│  │  Alpha (OpenWrt) │◄───────────►│  Host / Debian   │        │
│  │  br-lan: 10.99.99.1           │  10.99.99.2       │        │
│  │                                │  (NAT to internet)│        │
│  └──────────────────┘             └──────────────────┘        │
│         │ eth1 (DHCP)                                         │
│         │ 10.99.98.x                                          │
│  tg-upstream-br (L2 only)                                     │
│         │ 10.99.98.1                                          │
│  ┌──────────────────┐                                         │
│  │  Beta (OpenWrt)  │◄── tg-poc-br ──► Host                  │
│  │  br-lan: 10.99.99.11                                       │
│  │  eth1: 10.99.98.1 (static, dnsmasq DHCP)                  │
│  └──────────────────┘                                         │
│                                                                 │
│  Local mint: http://v1.testnut.lan:8385 → 10.99.99.2:8385     │
└─────────────────────────────────────────────────────────────────┘
```

### Network Details

| Bridge | Subnet | Purpose |
|--------|--------|---------|
| `tg-poc-br` | 10.99.99.0/24 | Management LAN. Both routers + host connected. |
| `tg-upstream-br` | 10.99.98.0/24 | L2-only bridge for Alpha↔Beta upstream link. No host port. |

| Host | Interface | IP | Role |
|------|-----------|-----|------|
| Alpha | br-lan | 10.99.99.1 | Primary TollGate under test |
| Alpha | eth1 | DHCP from Beta | WAN/upstream interface |
| Beta | br-lan | 10.99.99.11 | Upstream TollGate (seller) |
| Beta | eth1 | 10.99.98.1 (static) | DHCP server for Alpha's WAN |
| Host/Debian | — | 10.99.99.2 | NAT gateway, mint host |

### Beta's Upstream Configuration

The cloud worker (`lib/cloud_lab/worker.py:_configure_beta_upstream()`) configures Beta as a DHCP server + NAT gateway for Alpha:

1. **eth1 static IP**: `10.99.98.1/24`
2. **dnsmasq**: DHCP range `10.99.98.10–60`
3. **nftables NAT**: Masquerade `10.99.98.0/24` → br-lan (`10.99.99.0/24`)
4. **nftables forward**: Accept traffic from `10.99.98.0/24`

This gives Alpha internet access through Beta — the foundation for the TollGate upstream payment model.

## Test Suite

File: `tests/scenarios/test_two_router_cloud.py`

### test_alpha_wan_link_to_beta

Verifies the L3 link between Alpha and Beta:

1. Alpha's eth1 has a DHCP lease in 10.99.98.0/24
2. Alpha can ping 10.99.98.1 (Beta's upstream IP)
3. SSH to Beta works

This is the foundation — without this link, no upstream TollGate flow is possible.

### test_block_mint_enters_degraded_mode

Verifies degraded mode when the configured mint is blocked:

1. Read the first mint URL from `/etc/tollgate/config.json`
2. Block it via `/etc/hosts` (`0.0.0.0 <hostname>`)
3. Restart `tollgate-wrt`
4. Poll the HTTP API until it returns kind 21023 (degraded notice)
5. Unblock mint in finally block

Uses HTTP API-based detection (`is_degraded()`, `wait_for_degraded()`) instead of CLI socket so it works on any backend version.

### test_unblock_mint_recovers_from_degraded

Verifies recovery from degraded mode:

1. Block mint → wait for degraded (same as above)
2. Unblock mint
3. Poll HTTP API until it returns kind 10021 (full merchant) with `price_per_step` tags
4. Assert recovery within 60s

### test_both_routers_healthy

Verifies both routers have running TollGate instances via HTTP API:

1. GET `/` on Alpha → expect kind 10021 (merchant) or 21023 (degraded)
2. GET `/` on Beta → expect kind 10021 or 21023

Both kinds are acceptable — the test only verifies TollGate is running and responding, not that it's in a specific state.

## Running

```bash
# Submit two-router cloud run
./scripts/cloud-lab.py submit --branch main --two-router --publish

# Check status
./scripts/cloud-lab.py status-run --run-id <run-id>
```

The `--two-router` flag triggers:
1. A second OpenWrt VM (Beta) is launched alongside Alpha
2. `tg-upstream-br` bridge is created
3. Beta is configured as upstream DHCP server + NAT gateway
4. `TOLLGATE_SECONDARY_ROUTER_HOST` is set in the test `.env`
5. `test_two_router_cloud.py` is included in the pytest run

## Design Decisions

### HTTP API over CLI Socket

The degraded mode tests use HTTP API responses (`GET /`, kind 10021/21023) instead of the CLI socket (`/var/run/tollgate.sock`). This is because:

- The CLI socket may not exist on all backend versions
- The HTTP API is always available when TollGate is running
- `is_degraded()` and `wait_for_degraded()` from `lib/helpers.py` provide reusable HTTP-based detection
- The tests can run against both Go and Rust backends

### No External Internet Requirement

Tests verify link-layer connectivity, not external internet access. Alpha pings Beta (10.99.98.1), not 8.8.8.8. This keeps the test self-contained and avoids dependencies on the GCP VM's external NAT.

### Mint Blocking via /etc/hosts

Instead of iptables (which would block all traffic to the mint IP), the tests add `0.0.0.0 <hostname>` to `/etc/hosts`. This is:
- Reversible (remove the line)
- Non-destructive (doesn't affect other routes)
- Consistent with the `router.block_mint()` / `router.unblock_mint()` API

## Previous Issues & Fixes

### CLI Socket Gating (commit bbf11ed)

The `test_status_command_works` test in `test_mint_health.py` was failing because it used `get_tollgate_status()` (CLI socket) unconditionally. Fixed by gating on CLI socket availability — now skips cleanly in cloud lab.

### Visual Test Timeout (commit bbf11ed)

`test_visual_happy_path` was timing out at 180s in QEMU cloud lab because Playwright/Chromium startup is slower. Fixed by:
- Increasing test timeout to 300s
- Increasing portal_ready timeout to 90s
- Adding early Playwright health check to skip cleanly if Chromium can't start

### HTTP-Based Degraded Detection (commit bbf11ed)

The three degraded/status tests were all skipping because `_skip_if_no_degraded_support()` relied on the CLI socket. Rewrote to use HTTP API (`is_degraded()`, `is_full_merchant()`, `wait_for_degraded()`) as primary detection mechanism, with CLI socket as secondary check.

## Related Files

| File | Purpose |
|------|---------|
| `tests/scenarios/test_two_router_cloud.py` | Test suite |
| `lib/cloud_lab/worker.py` | Cloud worker (VM setup, Beta configuration) |
| `lib/cloud_lab/constants.py` | `SELLER_OPENWRT_IP`, bridge names, mint URLs |
| `lib/helpers.py` | `is_degraded()`, `wait_for_degraded()`, `is_full_merchant()` |
| `lib/router.py` | `block_mint()`, `unblock_mint()`, `api_body()` |
| `scripts/cloud-lab.py` | CLI for submitting runs |
