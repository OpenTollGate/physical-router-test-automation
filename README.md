# physical-router-test-automation

End-to-end Playwright tests for [tollgate-module-basic-go](https://github.com/OpenTollGate/tollgate-module-basic-go) running against a physical OpenWrt router.

These tests cannot run in GitHub CI — they require a real router on the local network with TollGate installed and LuCI accessible.

## Prerequisites

- macOS or Linux host on the same LAN as the router
- Go 1.24+ (for building the ipk)
- Python 3.12 (for the cashu CLI)
- Node.js 18+ (for Playwright)
- `sshpass` (`brew install sshpass` or `apt install sshpass`)

## Setup

```bash
# 1. Install the cashu CLI (needed for fund/drain token tests)
./scripts/setup-cashu.sh

# 2. Install Playwright
npm install
npx playwright install
```

## Deploy to Router

Builds the ipk from a given git hash and installs it on the router:

```bash
TOLLGATE_LUCI_PASSWORD=<password> ./scripts/deploy.sh <git-hash>
```

Example:

```bash
TOLLGATE_LUCI_PASSWORD=secretpass ./scripts/deploy.sh feat/luci-admin-ui
```

Takes a branch name, tag, or commit hash. Defaults to router at `192.168.13.112`.

## Run Tests

```bash
TOLLGATE_LUCI_PASSWORD=<password> ./scripts/run-tests.sh [desktop|mobile]
```

Defaults to `desktop` viewport. The full suite runs ~3 minutes with 35 tests.

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TOLLGATE_LUCI_PASSWORD` | Yes | — | Router SSH and LuCI password |
| `TOLLGATE_LUCI_URL` | No | `http://192.168.13.112:8080` | LuCI admin URL |
| `TOLLGATE_LUCI_USER` | No | `root` | LuCI/SSH username |
| `TOLLGATE_SSH_HOST` | No | `192.168.13.112` | Router IP for SSH |
| `TOLLGATE_VIEWPORT` | No | `desktop` | Viewport: `desktop` or `mobile` |

## Test Categories

- **Tab loading** — all 5 tabs render without errors
- **Dashboard** — restart modal, fund warning, drain modal
- **Network** — show password, rename SSID round-trip, change password
- **Configuration** — profit share sliders, add/remove mint/share/identity, save round-trip
- **Advanced** — JSON validation, reload files, identity editor
- **Fund/Drain** — real testnut.cashu.exchange tokens, SSH file verification, lifecycle round-trips

## cashu CLI Notes

The test suite uses [cashu](https://github.com/cashubtc/cashu) to mint testnet tokens from `testnut.cashu.exchange` (a FakeWallet mint that auto-pays invoices). The `setup-cashu.sh` script applies a one-line patch to cashu's `models.py` to handle a version mismatch with the testnut mint's API (missing `active` field on keysets).

---

## Manual Hardware Test Suites

A top-level **Makefile** provides convenient targets that wrap all test suites. Run `make help` for the full list.

### Quick Start

```bash
# 1. Install dependencies
make setup

# 2. Configure routers
cp mint-health/routers.env.example mint-health/routers.env     # edit with real IPs/passwords
cp upstream-wifi/routers.env.example upstream-wifi/routers.env

# 3. Run tests
make smoke-degraded ROUTER=alpha           # single-router degraded lifecycle (~3 min)
make smoke-upstream                        # two-router payment test (~5 min)
make test-captive-portal ROUTER=alpha      # Playwright captive portal tests
make test-cashu-payment ROUTER=alpha       # e2e cashu payment test
make full-all ROUTER=alpha                 # everything combined
```

### Directory Structure

```
Makefile              # Top-level runner — make <target> ROUTER=alpha
upstream-wifi/        # Upstream WiFi daemon tests (scan, connect, switch, reseller mode)
  Makefile            # Direct: make -f upstream-wifi/Makefile r-smoke ROUTER=alpha SSID=x PASS=y
mint-health/          # Mint health tests (degraded mode, offline payment, recovery)
  Makefile            # Direct: make -f mint-health/Makefile r-smoke-degraded ROUTER=alpha
```

### Mint Health Test Quick Reference

```bash
# Single-router degraded mode lifecycle (~3 min)
make smoke-degraded ROUTER=alpha

# Two-router combined test (~5 min)
make smoke-upstream                          # Scenario A: connect online, then degrade
make smoke-degraded-connect                  # Scenario B: connect while degraded (RISKY)

# Dynamic rebuild test (~10 min)
make smoke-dynamic-rebuild ROUTER=alpha

# STA health check
make check-sta-health ROUTER=alpha
```

See `mint-health/docs/router-test-plan.md` for the full test plan.

### Upstream WiFi Test Quick Reference

```bash
# Smoke test (~5 min)
make smoke-upstream-full ROUTER=alpha SSID=MyNet PASS=secret

# Full test suite (~30 min)
make full-upstream ROUTER=alpha SSID=MyNet PASS=secret

# STA health check
make check-sta-health ROUTER=alpha
```

See `upstream-wifi/docs/upstream-wifi-test-report.md` for test results and `docs/router-b-incident-2026-04-30.md` for incident notes.

## Serial Console Integration

In addition to SSH, routers can be managed via USB-TTL serial connections. Serial works even when the router has no network connectivity (during cold boot, WiFi reload, or NetBird outages).

### Setup

```bash
# Install serial dependencies
pip3 install -r scripts/requirements-serial.txt

# Add serial port config to routers.env
# See mint-health/routers.env.example for the SERIAL fields
```

See `docs/serial-integration-plan.md` for the full hardware setup guide (USB-TTL adapters, udev rules, mini PC orchestrator).

### Target Prefix Convention

| Prefix | Transport | Use Case |
|--------|-----------|----------|
| `r-`   | SSH       | Normal operations (existing) |
| `s-`   | Serial    | No-network scenarios: cold boot, recovery, monitoring |
| `h-`   | Hybrid    | Tries SSH first, falls back to serial if unreachable |

### Serial Quick Reference

```bash
# Interactive serial console
make serial-shell ROUTER=alpha

# Watch full boot output (reboot or power-cycle the router)
make serial-cold-boot ROUTER=alpha

# Emergency recovery on a stranded router
make serial-recovery ROUTER=alpha CMD="sed -i '/nofee.testnut/d' /etc/hosts && /etc/init.d/tollgate-wrt restart"

# Hybrid: SSH first, serial fallback
make hybrid-status ROUTER=alpha
make hybrid-cleanup ROUTER=alpha
```
