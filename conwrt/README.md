# conwrt Tests

Tests for [conwrt](https://github.com/amperstrand/conwrt) router configuration
use cases against real OpenWrt systems (physical routers or QEMU VMs).

## Prerequisites

- OpenWrt router accessible via SSH (physical or QEMU VM)
- conwrt repo checked out locally
- `iperf3` installed on router and client (for bufferbloat tests)

## Configuration

Set environment variables (or add to `.env`):

```bash
CONWRT_ROUTER_HOST=192.168.1.1       # Router IP
CONWRT_ROUTER_KEY=~/.ssh/id_ed25519  # SSH key (optional)
CONWRT_ROUTER_PORT=22                # SSH port
CONWRT_CLIENT_HOST=192.168.1.100     # Client for iperf3 tests (optional)
CONWRT_REPO=~/src/conwrt             # Path to conwrt checkout
```

## Running Tests

```bash
# From the physical-router-test-automation repo root
source ~/.tollgate-test-venv/bin/activate

# Run all conwrt tests
pytest conwrt/ -v

# Run only use case config tests
pytest conwrt/test_use_cases.py -v

# Run a specific use case
pytest conwrt/test_use_cases.py -v -k adguard

# Run only SQM config tests (no client needed)
pytest conwrt/test_sqm_functional.py -v -k "not bufferbloat"

# Run full bufferbloat test (needs client host)
pytest conwrt/test_sqm_functional.py::test_sqm_reduces_bufferbloat -v

# Run MPTCP bonding tests (needs BSBF server + MPTCP kernel)
pytest conwrt/test_mptcp_bonding.py -v
```

## Cloud Lab (SHC/GCP)

```bash
# Submit conwrt SQM test to SHC cloud
./scripts/cloud-lab.py submit --cloud shc \
  --suite conwrt \
  --branch main \
  --publish
```

## QEMU VM Test Runner

`run_use_case_tests.py` boots an OpenWrt QEMU VM and runs all use cases
end-to-end, capturing evidence and publishing to Nostr/Blossom:

```bash
# Full run with evidence publishing
python3 conwrt/run_use_case_tests.py \
  --openwrt-img /tmp/openwrt.img \
  --nsec ~/.config/prta/nsec \
  --blossom-server https://blossom.psbt.me

# Single use case, skip publishing
python3 conwrt/run_use_case_tests.py \
  --openwrt-img /tmp/openwrt.img \
  --use-case sqm \
  --skip-publish
```

## Use Case Test Inventory

All 16 use cases in `test_use_cases.py` (parametrized, run against a real router):

| Use Case | What it verifies | Packages |
|----------|-----------------|----------|
| `ssh-hardening` | PasswordAuth off, RootPasswordAuth off | — |
| `sqm` | CAKE qdisc on eth0, correct speeds | sqm-scripts |
| `doh` | https-dns-proxy resolver_url set | https-dns-proxy |
| `wireguard-client` | wg0 interface, peer endpoint configured | wireguard-tools, kmod-wireguard |
| `nodns` | dnsmasq nodns domain + server | — |
| `mwan3` | mwan3 status shows wan interface | mwan3, iptables-nft |
| `pbr` | pbr.config enabled, nft_file_helper | pbr |
| `adguard` | AdGuard Home enabled, dnsmasq forwards to it | adguardhome |
| `auto-sqm` | auto_sqm.config with static speeds | sqm-scripts, iperf3 |
| `guest-wifi` | Guest network/firewall zone (REJECT) | — |
| `openclash` | OpenClash config with Meta core | luci-app-openclash, bash |
| `ssl` | uhttpd HTTPS on port 443 with cert | libustream-wolfssl |
| `tollgate-security` | RFC 1918 DROP rules on firewall | — |
| `travelmate` | travelmate enabled with radio0 | travelmate |
| `vpn-node` | VPN listing script + nsec file | wireguard-tools |
| `wireguard-server` | wg0 server interface + firewall zones | wireguard-tools, qrencode |

## Other Test Files

| Test File | What it verifies | Needs client? |
|-----------|-----------------|---------------|
| `test_sqm_functional.py` | SQM installation, UCI state, qdisc, bufferbloat | Bufferbloat: Yes |
| `test_mptcp_bonding.py` | MPTCP kernel, BSBF endpoints, throughput, failover | No (needs BSBF server) |
| `test_vpn_e2e.py` | VPN payment flow end-to-end (Cashu → VPS) | No (needs nodns.shop) |
| `publish_results.py` | Nostr/Blossom evidence publishing wrapper | No |

## Evidence Publishing

Test results are published as kind 30078 Nostr events with Blossom file attachments.
View live results at [tests.tollgate.me](https://tests.tollgate.me) (select "conwrt" tab).
