# physical-router-test-automation

Test automation for [tollgate-module-basic-go](https://github.com/OpenTollGate/tollgate-module-basic-go) running against a physical OpenWrt router.

Two test suites run side by side:

| Suite | Runner | What it tests | Tests |
|-------|--------|--------------|-------|
| **LuCI Admin UI** | Playwright | Dashboard, network, config, wallet, payment protocol, data allotment | 35+ |
| **API + Phone** | pytest | Payment protocol, sessions, captive portal, metering, edge cases | 51 |

These tests cannot run in GitHub CI — they require a real router on the local network.

## Prerequisites

- macOS or Linux host on the same LAN as the router
- Python 3.12+ (for pytest and cashu CLI)
- Node.js 18+ (for Playwright)
- SSH key access to the router (recommended) or `sshpass` (Playwright only)
- `nak` CLI for Nostr event signing (Playwright payment protocol tests)

## Setup

```bash
# 1. Install Python test dependencies
./scripts/setup-python.sh

# 2. Install the cashu CLI (needed for token minting)
./scripts/setup-cashu.sh

# 3. Install Playwright
npm install
npx playwright install
```

## Deploy to Router

Builds the ipk from a given git hash and installs it on the router:

```bash
./scripts/deploy.sh <git-hash>
```

Takes a branch name, tag, or commit hash.

## Run Tests

### pytest (API + Phone)

```bash
# Quick sanity check (~2s, API-only)
make smoke

# API tests only (~40s, no phone)
make api

# Phone tests (requires Android via ADB)
make phone

# Full suite
make extended

# From macOS (no phone)
make api-mac

# From Linux (no phone)
make api-linux

# Everything: LuCI + API + Phone
./scripts/run-all.sh

# Publish mode — only safe screenshots in report
make api PFLAGS="--publish"
```

Runner scripts capture results to `results/<timestamp>-<sha>/raw/`.

### Playwright (LuCI Admin UI)

```bash
./scripts/run-tests.sh [desktop|mobile] [router-id]
```

Defaults to `desktop` viewport.

### Test Markers (pytest)

| Marker | Meaning | Tier |
|--------|---------|------|
| `smoke` | Quick sanity check (~2s, API-only) | 1 |
| `critical` | Core functionality (~2min) | 2 |
| `extended` | Full suite including edge cases (~10min) | 3 |
| `api` | API-only, no phone needed | — |
| `phone` | Requires phone via ADB (or desktop client) | — |
| `config` | Modifies router pricing/metric config | — |
| `slow` | Takes >60s (session expiry waits) | — |
| `android_only` | Requires physical Android device | — |
| `publish_screenshot` | Screenshot safe for published reports | — |

Tier hierarchy: `smoke ⊂ critical ⊂ extended`. Running `-m critical` includes all smoke tests.

### Client Modes (pytest)

| Flag | WiFi client | Phone tests | Notes |
|------|------------|-------------|-------|
| `--client=adb` (default) | Android phone via ADB | All 51 tests | Requires PHONE_SERIAL |
| `--client=mac` | macOS via networksetup | 45 tests (1 skipped) | Auto-detects WiFi MAC and IP |
| `--client=linux` | Linux via nmcli | 45 tests (1 skipped) | Auto-detects WiFi MAC and IP |

## Results Pipeline

```bash
make sanitize   # Redact PII from latest run
make publish    # Publish to gh-pages
```

`sanitize-results.sh` redacts MACs, IPs, tokens, SSIDs, serials, and local paths. Review sanitized output before publishing.

## Environment Variables

Copy `.env.example` to `.env` and fill in your values. All config is via environment variables — no secrets in git.

| Variable | Required | Default | Description |
|---|---|---|---|
| `TOLLGATE_SSH_HOST` | Yes | — | Router IP for SSH |
| `TOLLGATE_SSH_KEY` | Recommended | `~/.ssh/id_ed25519` | SSH private key for router access |
| `TOLLGATE_LUCI_URL` | No | — | LuCI admin URL |
| `TOLLGATE_LUCI_USER` | No | `root` | LuCI username |
| `TOLLGATE_LUCI_PASSWORD` | For Playwright | — | LuCI password (Playwright tests) |
| `TOLLGATE_SSID` | No | `TollGate` | TollGate WiFi SSID |
| `TOLLGATE_DOMAIN` | No | — | Portal domain (e.g. `tollgate.local`) |
| `PHONE_SERIAL` | For ADB mode | — | Android device serial |
| `TOLLGATE_ROUTER_ID` | No | — | Router ID from `config/routers.json` |
| `TOLLGATE_ROUTER_INVENTORY` | No | `config/routers.json` | Path to router inventory file |
| `TOLLGATE_VIEWPORT` | No | `desktop` | Playwright viewport |
| `TOLLGATE_ENABLE_WIFI_CLIENT_TESTS` | No | `false` | Enable tests that change host WiFi |
| `TOLLGATE_ENABLE_DATA_ALLOTMENT_TESTS` | No | `false` | Enable bandwidth consumption tests |
| `TOLLGATE_TEST_MINT_URL` | No | `https://testnut.cashu.exchange` | Cashu mint URL for test tokens |
| `TOLLGATE_PUBLISH` | No | `false` | Publish test report to gh-pages |

Full list in `.env.example`.

## Test Coverage

### LuCI Admin UI (Playwright)

- **Tab loading** — all 5 tabs render without errors
- **Dashboard** — restart modal, fund warning, drain modal
- **Network** — show password, rename SSID round-trip, change password
- **Configuration** — profit share sliders, add/remove mint/share/identity, save round-trip
- **Advanced** — JSON validation, reload files, identity editor
- **Fund/Drain** — real testnut.cashu.exchange tokens, SSH file verification, lifecycle round-trips
- **Payment protocol** — discovery event, Cashu payment, Nostr signing, connectivity verification
- **Data allotment** — bandwidth consumption until paid allotment closes connectivity
- **Router network config** — OpenWrt station-mode UCI configuration
- **Reboot recovery** — service restart, state recovery
- **Firmware upgrade** — ethernet hotplug flashing

### API Tests (pytest)

- **Health & discovery** — backend status, TIP-01 info endpoint, RFC 8908 captive portal API
- **Payment structure** — POST / response codes, NUT-24 headers, wrong mint rejection, minimum token
- **CGI endpoints** — pending token write/read/consume, notice events, log beacon, session state
- **Concurrency** — concurrent payments with same/different tokens

### Phone Tests (pytest)

- **Payment flows** — direct payment, V3/V4 token formats, paste delivery, URL param handoff
- **Session lifecycle** — short session expiry, session extension, backend restart, WiFi reconnect
- **Metering** — time-based and data-based metering accuracy
- **Edge cases** — spent token reuse, invalid token, re-auth after expiry, deauth on expiry

## cashu CLI Notes

The test suite uses [cashu](https://github.com/cashubtc/cashu) to mint testnet tokens from `testnut.cashu.exchange` (a FakeWallet mint that auto-pays invoices). The `setup-cashu.sh` script applies a one-line patch to handle a version mismatch with the testnut mint's API.

## Multi-Router Inventory

For multiple router models, copy `config/routers.example.json` to `config/routers.json` and set `TOLLGATE_ROUTER_ID`. The private inventory file is ignored by git.

```bash
cp config/routers.example.json config/routers.json
TOLLGATE_ROUTER_ID=lab-router-a ./scripts/run-tests.sh <tollgate-commit>
```

## Privacy

- No MACs, SSIDs, IPs, passwords, tokens, or phone serials in committed code
- All config via environment variables (`.env.example` has placeholders)
- `sanitize-results.sh` redacts all PII before publication
- Router inventory (`config/routers.json`) is gitignored
