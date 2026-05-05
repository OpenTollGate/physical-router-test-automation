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
