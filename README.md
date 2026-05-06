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
TOLLGATE_LUCI_PASSWORD=<password> ./scripts/run-tests.sh <tollgate-commit> [desktop|mobile] [router-id]
```

Defaults to `desktop` viewport. The full UI suite runs against a physical router and writes `test-run-*/run.json` plus an HTML report.

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TOLLGATE_LUCI_PASSWORD` | Yes | — | Router SSH and LuCI password |
| `TOLLGATE_LUCI_URL` | No | `http://192.168.13.112:8080` | LuCI admin URL |
| `TOLLGATE_LUCI_USER` | No | `root` | LuCI/SSH username |
| `TOLLGATE_SSH_HOST` | No | derived from `TOLLGATE_LUCI_URL` | Router IP for SSH |
| `TOLLGATE_SSH_PASSWORD` | No | falls back to `TOLLGATE_LUCI_PASSWORD` | Separate SSH password |
| `TOLLGATE_SSH_USER` | No | `root` | SSH username |
| `TOLLGATE_VIEWPORT` | No | `desktop` | Viewport: `desktop` or `mobile` |
| `TOLLGATE_ROUTER_ID` | No | — | Router ID from `config/routers.json` |
| `TOLLGATE_ROUTER_INVENTORY` | No | `config/routers.json` | Path to router inventory file |
| `TOLLGATE_ROUTER_MODEL` | No | `unknown` | Router model identifier |
| `TOLLGATE_ROUTER_ARCH` | No | `aarch64_cortex-a53` | Router architecture for ipk builds |
| `TOLLGATE_WIFI_INTERFACE` | No | — | Host WiFi interface for client tests |
| `TOLLGATE_SSID_PREFIX` | No | `TollGate-` | Prefix for TollGate WiFi SSIDs |
| `TOLLGATE_UPSTREAM_SSID` | No | — | Upstream WiFi SSID for station-mode tests |
| `TOLLGATE_UPSTREAM_WIFI_PASSWORD` | No | — | Upstream WiFi password |
| `TOLLGATE_ENABLE_WIFI_CLIENT_TESTS` | No | `false` | Enable tests that change host WiFi |
| `TOLLGATE_ENABLE_DATA_ALLOTMENT_TESTS` | No | `false` | Enable bandwidth consumption tests |
| `TOLLGATE_PAYMENT_STEPS` | No | `100` | Number of payment steps for protocol tests |
| `TOLLGATE_CONNECTIVITY_HOST` | No | `8.8.8.8` | Host to ping for connectivity checks |
| `TOLLGATE_TEST_MINT_URL` | No | `https://testnut.cashu.exchange` | Cashu mint URL for test tokens |
| `TOLLGATE_DATA_TEST_URL` | No | `https://nbg1-speed.hetzner.com/100MB.bin` | URL for data allotment download |
| `TOLLGATE_DATA_TEST_TIMEOUT` | No | `300` | Timeout in seconds for data test |
| `TOLLGATE_ETHERNET_INTERFACES` | No | — | Comma-separated ethernet interfaces for flashing |
| `TOLLGATE_FIRMWARE_IMAGE` | No | — | Path to firmware image for flashing |
| `TOLLGATE_PUBLISH` | No | `false` | Publish test report to gh-pages |
| `TOLLGATE_BRANCH` | No | — | Branch name for report metadata |
| `TOLLGATE_PR` | No | — | PR number for report metadata |
| `TOLLGATE_GH_PAGES_KEEP` | No | `10` | Number of report runs to keep on gh-pages |

## Test Categories

- **Tab loading** — all 5 tabs render without errors
- **Dashboard** — restart modal, fund warning, drain modal
- **Network** — show password, rename SSID round-trip, change password
- **Configuration** — profit share sliders, add/remove mint/share/identity, save round-trip
- **Advanced** — JSON validation, reload files, identity editor
- **Fund/Drain** — real testnut.cashu.exchange tokens, SSH file verification, lifecycle round-trips

## cashu CLI Notes

The test suite uses [cashu](https://github.com/cashubtc/cashu) to mint testnet tokens from `testnut.cashu.exchange` (a FakeWallet mint that auto-pays invoices). The `setup-cashu.sh` script applies a one-line patch to cashu's `models.py` to handle a version mismatch with the testnut mint's API (missing `active` field on keysets).


## Migrated Physical-Router Coverage

The framework now keeps the Playwright LuCI UI suite and adds opt-in physical-router coverage extracted from the old `tollgate-module-basic-go/tests` directory:

- `tests/router-network-config.spec.mjs` — OpenWrt `wwan`/station-mode UCI configuration and network restart verification.
- `tests/tollgate-payment-protocol.spec.mjs` — TollGate discovery event, Cashu payment token, Nostr payment event signing via `nak`, and client connectivity verification.
- `tests/data-allotment.spec.mjs` — bandwidth consumption until the paid data allotment closes connectivity ([detailed docs](docs/data-allotment-testing.md)).
- `scripts/flash-routers.mjs` — ethernet hotplug firmware flashing utility for physical routers.

Network-changing tests are opt-in and skip unless their required `TOLLGATE_*` environment variables are set. No router passwords, upstream WiFi credentials, firmware image paths, reports, screenshots, or generated results belong in git.

## Multi-Router Inventory

For multiple router models, copy `config/routers.example.json` to `config/routers.json` and set `TOLLGATE_ROUTER_ID`. The private inventory file is ignored by git.

```bash
cp config/routers.example.json config/routers.json
TOLLGATE_ROUTER_ID=lab-router-a ./scripts/run-tests.sh <tollgate-commit>
```
