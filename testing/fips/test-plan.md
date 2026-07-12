# FIPS Multi-Node TollGate Integration Test

Tests the per-peer forwarding policy on a real FIPS mesh. Three SHC VMs
form a FIPS mesh over UDP. Node A (the gateway) controls transit access
via `set_peer_policy`. Node B (the buyer) attempts to reach Node C
(the internet exit) through Node A.

## Topology

```
  VM-B (buyer)        VM-A (gateway)       VM-C (exit)
  FIPS daemon         FIPS daemon          FIPS daemon
  10.99.1.2           10.99.1.1            10.99.1.3
      └──── FIPS mesh (UDP :2121) ────┘
             ╲                   ╱
              ╲    transit      ╱
               B ──→ A ──→ C
```

## Prerequisites

- 3 SHC VMs (Standard 2C/8GB), Debian 13
- Amperstrand/fips `feat/tollgate-peer-policy` branch
- Rust toolchain installed on each VM
- Each VM has a public IP for FIPS UDP transport

## Test Scenarios

### Scenario 1: Default LocalOnly blocks transit
1. All three nodes boot FIPS, connect as mesh peers
2. Node B attempts `ping6` to Node C through the mesh
3. **Expected**: FAIL — Node A's default policy is LocalOnly, transit blocked

### Scenario 2: set_peer_policy enables transit
1. On Node A: `fipsctl set-peer-policy <B-npub> full`
2. Node B attempts `ping6` to Node C through the mesh
3. **Expected**: SUCCESS — transit now allowed

### Scenario 3: Revoke transit
1. On Node A: `fipsctl set-peer-policy <B-npub> local_only`
2. Node B attempts `ping6` to Node C again
3. **Expected**: FAIL — transit revoked

### Scenario 4: show_peers reports policy
1. On Node A: `fipsctl show peers`
2. **Expected**: output contains `"forwarding_policy": "local_only"` for Node B

## Pass Criteria

- Scenario 1: ping6 fails (transit blocked by default)
- Scenario 2: ping6 succeeds (transit allowed after policy change)
- Scenario 3: ping6 fails (transit revoked)
- Scenario 4: JSON output contains forwarding_policy field
