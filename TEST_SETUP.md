# Physical Router Test Setup

## Hardware

Two GL.iNet MT3000 routers (MT7981B, dual-band WiFi 6, arm64) running OpenWrt 24.10.4.

| Role | Label | NetBird IP | LAN IP | Open AP | PSK AP |
|------|-------|------------|--------|---------|--------|
| Primary test target | alpha | 100.90.41.166 | 192.168.41.1 | TollGate-1690 | c08r4d0r-1690 |
| Secondary / upstream | beta | 100.90.216.248 | 172.19.217.1 | TollGate-D1C6 | c03rad0r-D1C6 |

Both routers share a PSK password (configured in `routers.env`).

## Network Topology

```
[Dev Machine] ──ethernet── [Alpha LAN: 192.168.41.x]
        │                       │
        │                  alpha connects to
        │                  upstream WiFi (TP-Link_97E6,
        │                  StarGate, c03rad0r-D1C6, etc.)
        │
        └────NetBird mesh──── [Beta: 100.90.216.248]
                                    │
                               beta connects to
                               upstream WiFi (TP-Link_97E6)
                                    │
                               beta broadcasts two APs:
                                 TollGate-D1C6 (open)
                                 c03rad0r-D1C6 (PSK)
```

### Connectivity paths

| From | To | Method | Notes |
|------|----|--------|-------|
| Dev machine | Alpha | Ethernet to LAN | Always works, even when alpha has no upstream |
| Dev machine | Alpha | NetBird 100.90.41.166 | Requires alpha's upstream to be up |
| Dev machine | Beta | NetBird 100.90.216.248 | Requires beta's upstream to be up |
| Dev machine | Beta | Beta LAN 172.19.217.1 | Only reachable through alpha relay (different subnet) |
| Alpha | Beta LAN | Via WiFi STA (c03rad0r-D1C6) | Alpha gets DHCP on beta's subnet |
| Alpha | Beta | Via TollGate-D1C6 (open AP) | Same, but open network |

### Key constraint

If beta's upstream is disconnected, beta becomes unreachable from the dev machine (NetBird goes down, LAN is on a different subnet). Recovery options:
1. SSH through alpha relay: `ssh alpha "ssh root@172.19.217.1 '...'"` (requires alpha connected to beta's AP)
2. Safety-net: schedule a delayed `uci set + wifi reload` on beta before disconnecting
3. Physical: ethernet cable directly to beta's LAN port

## Test Binary Deployment

Cross-compile on dev machine, SCP to router:

```sh
GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -o /tmp/tollgate-wrt .
GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -o /tmp/tollgate-cli ./cmd/tollgate-cli

scp -O /tmp/tollgate-wrt /tmp/tollgate-cli root@192.168.41.1:/tmp/
ssh root@192.168.41.1 "cp /tmp/tollgate-wrt /usr/bin/ && cp /tmp/tollgate-cli /usr/bin/ && chmod +x /usr/bin/tollgate-wrt /usr/bin/tollgate-cli"
```

Note: `scp -O` is required because OpenWrt's ash doesn't have sftp-server.

## Test Automation

### Makefile

All test targets are in [`mint-health/Makefile`](mint-health/Makefile). Run from that directory:

```sh
cd ~/physical-router-test-automation/mint-health
make -f Makefile r-smoke-degraded ROUTER=alpha
```

### Configuration

Copy `routers.env.example` to `routers.env` and fill in credentials. This file is gitignored.

### Key test targets

| Target | What it tests | Duration |
|--------|--------------|----------|
| `r-smoke-degraded` | Degraded lifecycle: block mint → degraded boot → unblock → recover | ~3 min |
| `r-smoke-degraded-upstream` | Alpha connects to beta's AP, pays for upstream, degraded payment | ~5 min |
| `r-test-startup-hygiene` | Boot with dead STA + good STA enabled, verify auto-switch | ~2 min |
| `r-test-startup-hygiene-dead-only` | Boot with ONLY dead STA, disconnect other router, verify emergency scan recovery | ~3 min |
| `r-full` | Full test suite (degraded + upstream + edge cases) | ~20 min |
| `r-deploy` | Cross-compile and deploy binaries | ~1 min |
| `r-shell` | Interactive SSH session to router | — |
| `r-rescue-router` | Rescue offline router via the other router | ~3 min |

### Router mutex

All `r-*` targets acquire a lock file (`routers.lock`) to prevent concurrent test execution. Use `make -f Makefile r-lock PHASE="description"` and `make -f Makefile r-unlock`.

## WiFi STA Architecture

Each router has multiple STA sections in UCI wireless config. Only ONE should be enabled (`disabled=0`) at a time — dual-WWAN breaks routing.

```
Alpha STAs:
  upstream_c03rad0r_d1c6  (c03rad0r-D1C6, beta PSK)   — ACTIVE (current)
  upstream_tollgate_d1c6  (TollGate-D1C6, beta open)   — disabled
  upstream_stargate       (StarGate)                    — disabled
  upstream_tp_link_97e6   (TP-Link_97E6)                — disabled (independent upstream)
  upstream_fritz_box_7490_as (FRITZ!Box 7490 AS)        — disabled
  wifinet4                (c03rad0r)                     — disabled
```

### STA switching

Use `tollgate upstream connect <SSID> [password]` — never manual UCI edits. The CLI ensures:
- Only one STA enabled at a time
- Proper encryption settings
- WiFi reload + DHCP settle

### Signal landscape

TP-Link_97E6 and StarGate are independent upstreams (not going through either router). c03rad0r-D1C6 and TollGate-D1C6 go through beta. This distinction matters for tests that disconnect beta's upstream.

## Test Scenarios

### 1. Mint health degradation

Block mint via `/etc/hosts` → restart → verify degraded mode (offline wallet, cached balance) → unblock → verify recovery.

### 2. Startup connectivity hygiene

After power cycle, OpenWrt brings up whatever STAs have `disabled=0`. If a non-internet STA is enabled, `startupConnectivityCheck()` detects no internet and triggers emergency scan+switch. Tested with:
- Two STAs enabled (one dead, one good) — `r-test-startup-hygiene`
- Only dead STA enabled, other router disconnected — `r-test-startup-hygiene-dead-only`

### 3. Cross-radio DHCP

When switching STAs across different radios (e.g., radio0 → radio1), OpenWrt's netifd may not re-evaluate the wwan interface. The dual-trigger `ifup wwan` nudge fixes this: immediate cross-radio trigger + 15s timer fallback.

### 4. Dead-only boot recovery

The most rigorous test:
1. Disconnect beta's upstream (disable its STA + ifdown wwan) with 5-min safety net
2. On alpha: enable only TollGate-D1C6 (beta's open AP, no internet), disable all others
3. Reboot alpha
4. Startup check detects no internet → emergency scan → finds disabled candidate → switches
5. Verify internet recovered, restore everything

## Emergency Procedures

| Problem | Fix |
|---------|-----|
| Alpha stranded (no internet) | `ssh root@192.168.41.1 "tollgate upstream connect <working-ssid> <password>"` |
| Beta stranded (no internet) | `ssh root@100.90.216.248 "uci set wireless.upstream_tp_link_97e6.disabled=0; uci commit wireless; wifi reload"` or relay through alpha |
| DNS broken after wifi reload | `ssh root@<ip> "rm -f /etc/resolv.conf && echo nameserver 8.8.8.8 > /etc/resolv.conf"` |
| Both routers offline | Physical ethernet to LAN port, configure static IP on dev machine |
| Stale DHCP on phy0-sta0 | `ssh root@<ip> "ifconfig phy0-sta0 0.0.0.0"` then `ifup wwan` |
| NetBird down | Wait 60s for auto-reconnect, or `/etc/init.d/netbird restart` |

## Directory Structure

```
physical-router-test-automation/
  mint-health/
    Makefile              # Test targets
    routers.env           # Credentials (gitignored)
    routers.env.example   # Template
    docs/
      router-mutex.md     # Lock file protocol
      router-test-plan.md # Detailed test plan for degraded merchant
```

The tollgate module source code is in a separate repository (`tollgate-module-basic-go`).
