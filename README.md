# physical-router-test-automation

Multi-tier test framework for [tollgate-module-basic-go](https://github.com/OpenTollGate/tollgate-module-basic-go) running against physical OpenWrt routers. Combines Playwright LuCI UI tests, pytest API tests (no phone needed), and pytest phone tests (physical Android device via ADB).

These tests cannot run in GitHub CI. They require real routers on the local network with TollGate installed and LuCI accessible.

## Prerequisites

- macOS or Linux host on the same LAN as the router
- Python 3.12 (for pytest and cashu CLI)
- Node.js 18+ (for Playwright)
- `sshpass` (`brew install sshpass` or `apt install sshpass`)
- `gh` CLI (for `test-pr.sh` PR resolution)
- ADB (for phone tests, optional)
- Go 1.24+ (only for local `deploy.sh` builds)

## Setup

```bash
# 1. Copy environment template and fill in your values
cp .env.example .env
# edit .env with your router IP, password, etc.

# 2. Set up Python venv with pytest and dependencies
./scripts/setup-python.sh
source ~/.tollgate-test-venv/bin/activate

# 3. Install the cashu CLI (needed for fund/drain token tests)
./scripts/setup-cashu.sh

# 4. Install Playwright
npm install
npx playwright install
```

## Quick Start: Test a PR

The primary workflow for testing a pull request:

```bash
# Activate Python venv
source ~/.tollgate-test-venv/bin/activate

# Test PR #42 against your router (API tests only)
./scripts/test-pr.sh --pr 42

# Test a branch, reset router first, run all tests
./scripts/test-pr.sh --branch feat/luci-admin-ui --reset --test all

# Test and publish report to gh-pages
./scripts/test-pr.sh --pr 42 --test api --publish

# Specify a particular router
./scripts/test-pr.sh --pr 42 --router lab-router-a
```

`test-pr.sh` handles the full workflow: resolve PR to branch/commit, verify router connectivity, factory reset (if `--reset`), deploy, run tests, parse JUnit results, generate HTML report, and optionally publish.

## Deploy to Router

Two deployment paths are available:

### Option 1: CI-built artifact (recommended)

Download a production-grade `.ipk` from GitHub Actions and deploy it:

```bash
TOLLGATE_LUCI_PASSWORD=<password> ./scripts/deploy-ci.sh <branch> [run-id] [router-ip]
```

This downloads the artifact built by the same CI pipeline used for releases, copies it to the router, installs via `opkg`, restarts services, and verifies.

```bash
TOLLGATE_LUCI_PASSWORD=secretpass ./scripts/deploy-ci.sh feat/luci-admin-ui
```

Use `download-ci-artifact.sh` for the download step only (no deploy).

### Option 2: Local build

Build the ipk from source and deploy it:

```bash
TOLLGATE_LUCI_PASSWORD=<password> ./scripts/deploy.sh <git-hash>
```

Takes a branch name, tag, or commit hash. Defaults to router at `192.168.13.112`.

## Run Tests

### Individual test tiers

```bash
# Playwright LuCI UI tests
TOLLGATE_LUCI_PASSWORD=<password> ./scripts/run-tests.sh [tollgate-commit] [desktop|mobile] [router-id]

# pytest API tier only (no phone needed)
./scripts/run-api.sh

# pytest phone tier only (requires Android device via ADB)
./scripts/run-phone.sh

# All tiers: Playwright + pytest API + pytest phone
./scripts/run-all.sh
```

The Playwright `run-tests.sh` commit hash is optional. If omitted, it falls back to `TOLLGATE_BRANCH` then `HEAD`. The script verifies SSH connectivity before running. Defaults to `desktop` viewport. Writes `test-run-*/run.json` plus an HTML report.

### Makefile targets

| Target | What it runs |
|---|---|
| `make smoke` | Smoke tests (~15s, API-only) |
| `make critical` | Core functionality (~2min) |
| `make extended` | Full suite including edge cases (~10min) |
| `make api` | All API-marked tests |
| `make phone` | All phone-marked tests |
| `make test` | All pytest tests |
| `make luci` | Playwright LuCI UI tests |
| `make smoke-mac` / `make api-mac` / `make test-mac` | Same as above, using macOS WiFi client instead of ADB |
| `make smoke-linux` / `make api-linux` / `make test-linux` | Same, using Linux NetworkManager client |
| `make deploy` | Run `scripts/deploy.sh` |
| `make setup` | Install Python + Node dependencies |
| `make setup-python` | Create Python venv |
| `make sanitize` | Sanitize latest result set |
| `make publish` | Publish latest result set to gh-pages |
| `make clean` | Remove results and caches |

### Pytest tiers and markers

Markers are defined in `pytest.ini`. Tiers are hierarchical: `smoke` is a subset of `critical`, which is a subset of `extended`.

| Marker | Description |
|---|---|
| `smoke` | Quick sanity check (~15s, API-only) |
| `critical` | Core functionality (~2min) |
| `extended` | Full suite including edge cases (~10min) |
| `api` | API-only test, no phone needed |
| `phone` | Requires physical phone connected via ADB |
| `config` | Modifies router configuration (pricing/metric) |
| `slow` | Takes more than 30s |
| `android_only` | Requires Android device |
| `publish_screenshot` | Screenshot from this test is safe for published reports |
| `pr(N)` | Test introduced for upstream PR #N |

Phone-marked tests automatically get `flaky(reruns=1)` and `timeout(300s)`.

## Project Structure

```
physical-router-test-automation/
  .env                          # Local environment variables (gitignored)
  .env.example                  # Template for .env
  AGENTS.md                     # Operational knowledge for agents/humans
  Makefile                      # Common targets
  pytest.ini                    # Pytest configuration and markers
  requirements.txt              # Python dependencies
  package.json                  # Node/Playwright dependencies
  config/
    routers.example.json        # Router inventory template
    routers.json                # Private router inventory (gitignored)
  credentials/                  # Router credentials (gitignored)
  findings/                     # Per-PR test findings with campaign summary
  lib/                          # Shared Python library
    cashu.py                    # CashuMint helper
    router.py                   # SSH/router interaction
    helpers.py                  # Shared test helpers
    nostr.py                    # Nostr event helpers
    deploy.py                   # Deploy and factory reset logic
    constants.py                # Shared constants
    clients/
      adb.py                    # ADB device control
      desktop.py                # macOS/Linux WiFi clients
      wifi.py                   # WiFi connection management
  scripts/                      # 19 scripts (see below)
  tests/
    conftest.py                 # Shared pytest fixtures (router, adb, cashu, wifi, deploy)
    api/                        # 30 pytest API test files
    phone/                      # 15 pytest phone test files
    web/                        # Playwright LuCI UI tests
    destructive/                # Playwright destructive tests (reboot, firmware)
    protocol/                   # Playwright protocol tests (payment, data allotment)
    helpers/                    # Shared Playwright helpers
    report/                     # Generated reports
```

## Scripts

All 19 scripts in `scripts/`:

### Test execution

| Script | Purpose |
|---|---|
| `test-pr.sh` | **Primary workflow script.** Unified PR testing: deploy, run tests, generate report. Usage: `--pr N` or `--branch NAME`, `--reset`, `--test api\|all`, `--publish`, `--router ID` |
| `run-tests.sh` | Run Playwright LuCI UI tests against physical router |
| `run-api.sh` | Run pytest API tier (no phone needed). Writes JUnit XML + HTML report |
| `run-phone.sh` | Run pytest phone tier (requires ADB device). Longer timeout (300s per test) |
| `run-all.sh` | Run everything: Playwright LuCI + pytest API + pytest phone |

### Deployment

| Script | Purpose |
|---|---|
| `deploy-ci.sh` | Download CI-built `.ipk` from GitHub Actions and deploy to router (recommended) |
| `deploy.sh` | Build `.ipk` from source and deploy to router |
| `download-ci-artifact.sh` | Download CI artifact only (no deploy step) |

### Firmware and recovery

| Script | Purpose |
|---|---|
| `build-firmware.py` | Build clean OpenWrt firmware images via ASU API with embedded SSH key and random root password. Reads from `config/routers.json`. Usage: `--router ID`, `--flash`, `--key <path>` |
| `uboot-recover.py` | Automated U-Boot recovery for bricked routers. Supports GL.iNet GL-MT3000 and D-Link COVR-X1860. Uses pcap monitoring and event-driven state machine. Voice guidance on macOS |
| `flash-routers.mjs` | Bulk ethernet hotplug sysupgrade flashing. Disabled unless `TOLLGATE_ENABLE_SYSUPGRADE_FLASHING=true` |

### Reporting and publishing

| Script | Purpose |
|---|---|
| `generate-pr-comment.py` | Generate markdown PR comment from test results, grouped by upstream PR |
| `generate-run-metadata.py` | Generate `run.json` metadata by querying router version info over SSH |
| `strip-screenshots.sh` | Strip non-whitelisted screenshots from Playwright HTML reports (keeps only `publish_screenshot` annotated) |
| `sanitize-results.sh` | Redact sensitive data (IPs, passwords, tokens, MACs, phone serials) from test results for public publication |
| `publish-report.sh` | Publish test report to gh-pages with dashboard index. Purges old runs beyond `TOLLGATE_GH_PAGES_KEEP` |

### Setup

| Script | Purpose |
|---|---|
| `setup-cashu.sh` | Install and patch cashu CLI for testnet token minting |
| `setup-python.sh` | Create Python venv at `~/.tollgate-test-venv` with pytest and dependencies |

## Test Directories

### `tests/api/` (30 tests, API-only, no phone needed)

SSH directly to the router. No physical device required. Covers:

- Health endpoint, info endpoint, hostname
- Captive portal API, session endpoint, session expiry and scan
- Concurrent payments, pay response structure
- Crypto random password generation
- Degraded mode, discovery mints, dual mint
- Edge tokens, minimum token, pending token CGI
- Log beacon CGI, notice event
- LuCI admin UI
- Mint 502 handling, mint health, mint payout, mint URL normalization, wrong mint
- Netbird firewall
- NUT-24, profit share validation
- CLI version, CLI wallet

### `tests/phone/` (15 tests, requires Android device via ADB)

End-to-end through the captive portal on a real Android phone. Covers:

- Auto connect, paste URL, URL param
- Backend restart, edge cases
- Camera captive portal
- Data metering, time metering
- Expiry kick, extend session, short session
- Session persistence
- Token formats

### `tests/web/` (Playwright LuCI tests)

`tollgate.spec.mjs` - LuCI admin UI tests:
- **Tab loading** - all 5 tabs render without errors
- **Dashboard** - restart modal, fund warning, drain modal
- **Network** - show password, rename SSID round-trip, change password, enable/disable
- **Configuration** - profit share sliders, add/remove mint/share/identity, save round-trip, proportional squeeze
- **Advanced** - JSON validation, reload files, identity editor
- **Fund/Drain** - real testnut.cashu.exchange tokens, SSH file verification, lifecycle round-trips, drain-twice zero check

### `tests/destructive/` (Playwright, 2 tests)

- `reboot-recovery.spec.mjs` - router comes back online after reboot with settings intact, network connectivity restored
- `firmware-upgrade.spec.mjs` - safe `.ipk` package install by default; full sysupgrade intentionally skipped unless `TOLLGATE_ENABLE_SYSUPGRADE_TESTS` is set

### `tests/protocol/` (Playwright, 4 tests, require additional hardware or opt-in)

- `payment-lifecycle.spec.mjs` - payment flow from discovery to connectivity
- `data-allotment.spec.mjs` - bandwidth consumption until paid data allotment closes connectivity ([detailed docs](docs/data-allotment-testing.md))
- `router-network-config.spec.mjs` - OpenWrt `wwan`/station-mode UCI configuration and network restart verification
- `tollgate-payment-protocol.spec.mjs` - TollGate discovery event, Cashu payment token, Nostr payment event signing via `nak`, client connectivity verification

### `tests/helpers/` (shared Playwright modules)

| Module | Purpose |
|---|---|
| `command.mjs` | Run commands via SSH |
| `inventory.mjs` | Router inventory loading |
| `network.mjs` | Network configuration helpers |
| `payment-protocol.mjs` | Payment protocol test helpers |
| `router-config.mjs` | Router configuration management |
| `router-files.mjs` | Router file operations |
| `router-packages.mjs` | Package management (opkg) |
| `router-wallet.mjs` | Wallet fund/drain operations |
| `ssh.mjs` | SSH connection management |

### `tests/conftest.py` (shared pytest fixtures)

Session-scoped fixtures: `router` (SSH), `adb` (phone or desktop client), `cashu` (mint helper), `wifi` (WiFi management), `deploy_session` (auto-deploy before tests). Per-test fixtures: `connected_wifi`, `test_pricing`, `screenshot_portal`, `screenshot_raw`. Handles auto-screenshot on failure, phone UI XML capture, and debug logging.

## Shared Library (`lib/`)

| Module | Purpose |
|---|---|
| `cashu.py` | `CashuMint` helper for minting/burning testnet tokens |
| `router.py` | `Router` class for SSH interaction, state management, log collection |
| `helpers.py` | Shared test utility functions |
| `nostr.py` | Nostr event creation and signing helpers |
| `deploy.py` | Deploy branch from CI, factory reset, health checks |
| `constants.py` | Shared constants (step sizes, defaults) |
| `clients/adb.py` | `ADBDevice` for Android phone control (tap, swipe, screenshot, UI dump) |
| `clients/desktop.py` | `MacWiFiClient`, `LinuxWiFiClient` and adapters for desktop testing without a phone |
| `clients/wifi.py` | `WiFi` connection management (connect, disconnect, reconnect) |

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TOLLGATE_LUCI_PASSWORD` | Yes | - | Router SSH and LuCI password |
| `TOLLGATE_LUCI_URL` | No | `http://192.168.13.112:8080` | LuCI admin URL |
| `TOLLGATE_LUCI_USER` | No | `root` | LuCI/SSH username |
| `TOLLGATE_SSH_HOST` | No | derived from `TOLLGATE_LUCI_URL` | Router IP for SSH |
| `TOLLGATE_SSH_PASSWORD` | No | falls back to `TOLLGATE_LUCI_PASSWORD` | Separate SSH password |
| `TOLLGATE_SSH_USER` | No | `root` | SSH username |
| `TOLLGATE_SSH_KEY` | No | - | SSH key path used by pytest fixtures and `uboot-recover.py` |
| `TOLLGATE_ROUTER_ID` | No | - | Router ID from `config/routers.json` |
| `TOLLGATE_ROUTER_INVENTORY` | No | `config/routers.json` | Path to router inventory file |
| `TOLLGATE_ROUTER_MODEL` | No | `unknown` | Router model identifier |
| `TOLLGATE_ROUTER_ARCH` | No | `aarch64_cortex-a53` | Router architecture for ipk builds |
| `TOLLGATE_VIEWPORT` | No | `desktop` | Viewport: `desktop` or `mobile` |
| `TOLLGATE_SSID` | No | `TollGate` | TollGate WiFi SSID |
| `TOLLGATE_SSID_PREFIX` | No | `TollGate-` | Prefix for TollGate WiFi SSIDs |
| `TOLLGATE_WIFI_INTERFACE` | No | - | Host WiFi interface for client tests |
| `TOLLGATE_UPSTREAM_SSID` | No | - | Upstream WiFi SSID for station-mode tests |
| `TOLLGATE_UPSTREAM_WIFI_PASSWORD` | No | - | Upstream WiFi password |
| `TOLLGATE_DOMAIN` | No | - | Router domain for DNS-based tests |
| `TOLLGATE_CLIENT_IP` | No | auto-detected | Client IP for phone/desktop tests |
| `TOLLGATE_CLIENT_MAC` | No | auto-detected | Client MAC address |
| `TOLLGATE_ENABLE_WIFI_CLIENT_TESTS` | No | `false` | Enable tests that change host WiFi |
| `TOLLGATE_ENABLE_DATA_ALLOTMENT_TESTS` | No | `false` | Enable bandwidth consumption tests |
| `TOLLGATE_PAYMENT_STEPS` | No | `100` | Number of payment steps for protocol tests |
| `TOLLGATE_CONNECTIVITY_HOST` | No | `8.8.8.8` | Host to ping for connectivity checks |
| `TOLLGATE_TEST_MINT_URL` | No | `https://testnut.cashu.exchange` | Cashu mint URL for test tokens |
| `TOLLGATE_DATA_TEST_URL` | No | `https://nbg1-speed.hetzner.com/100MB.bin` | URL for data allotment download |
| `TOLLGATE_DATA_TEST_TIMEOUT` | No | `300` | Timeout in seconds for data test |
| `TOLLGATE_PACKAGE_PATH` | No | - | Path to `.ipk` for safe package-upgrade test |
| `TOLLGATE_ETHERNET_INTERFACES` | No | - | Comma-separated ethernet interfaces for sysupgrade flashing |
| `TOLLGATE_FIRMWARE_IMAGE` | No | - | Path to firmware image for sysupgrade test/flashing |
| `TOLLGATE_ENABLE_SYSUPGRADE_TESTS` | No | `false` | Enable full firmware sysupgrade test (requires external recovery) |
| `TOLLGATE_ENABLE_SYSUPGRADE_FLASHING` | No | `false` | Enable `scripts/flash-routers.mjs` sysupgrade flashing |
| `TOLLGATE_SYSUPGRADE_WIPE_CONFIG` | No | `false` | Add `sysupgrade -n`; dangerous, wipes SSH/network/LuCI config |
| `TOLLGATE_FIRMWARE_PASSWORD` | No | random | Override firmware root password in `build-firmware.py` |
| `TOLLGATE_EXPECTED_MAC` | No | - | Expected router MAC for factory reset guard |
| `TOLLGATE_CASHU_VENV` | No | `/tmp/cashu-venv` | Path to cashu CLI venv |
| `TOLLGATE_PYTHON_VENV` | No | `~/.tollgate-test-venv` | Path to Python test venv |
| `PHONE_SERIAL` | No | - | Android device serial for ADB (required for phone tests) |
| `PHONE_PIN` | No | - | Android device PIN for unlock |
| `TOLLGATE_PUBLISH` | No | `false` | Publish test report to gh-pages |
| `TOLLGATE_BRANCH` | No | - | Branch name for report metadata |
| `TOLLGATE_PR` | No | - | PR number for report metadata |
| `TOLLGATE_GH_PAGES_KEEP` | No | `10` | Number of report runs to keep on gh-pages |

## cashu CLI Notes

The test suite uses [cashu](https://github.com/cashubtc/cashu) to mint testnet tokens from `testnut.cashu.exchange` (a FakeWallet mint that auto-pays invoices). The `setup-cashu.sh` script applies a one-line patch to cashu's `models.py` to handle a version mismatch with the testnut mint's API (missing `active` field on keysets).

## Migrated Physical-Router Coverage

The framework keeps the Playwright LuCI UI suite and adds opt-in physical-router coverage extracted from the old `tollgate-module-basic-go/tests` directory. These now live in `tests/protocol/` and `tests/destructive/`:

- `tests/protocol/router-network-config.spec.mjs` - OpenWrt `wwan`/station-mode UCI configuration and network restart verification.
- `tests/protocol/tollgate-payment-protocol.spec.mjs` - TollGate discovery event, Cashu payment token, Nostr payment event signing via `nak`, and client connectivity verification.
- `tests/protocol/data-allotment.spec.mjs` - bandwidth consumption until the paid data allotment closes connectivity ([detailed docs](docs/data-allotment-testing.md)).
- `tests/protocol/payment-lifecycle.spec.mjs` - end-to-end payment flow from discovery to connectivity.
- `tests/destructive/firmware-upgrade.spec.mjs` - safe `.ipk` package install by default; full sysupgrade is intentionally skipped unless explicitly enabled.
- `scripts/flash-routers.mjs` - disabled-by-default ethernet hotplug sysupgrade utility for physical routers.

Routine development should prefer `TOLLGATE_PACKAGE_PATH=<package.ipk> npm test`: `opkg install` updates TollGate without changing Dropbear, firewall, LuCI/uhttpd, LAN/WAN, or wallet/config state. Full `sysupgrade` is reserved for release/image QA because stock firmware may not include this lab router's custom recovery-critical state (authorized SSH key, SSH from WAN, 8080 LuCI listener, custom LAN addressing). Only enable sysupgrade tests or flashing when you have a separate recovery path.

Network-changing tests are opt-in and skip unless their required `TOLLGATE_*` environment variables are set. No router passwords, upstream WiFi credentials, firmware image paths, reports, screenshots, or generated results belong in git.

## Multi-Router Inventory

For multiple router models, copy `config/routers.example.json` to `config/routers.json` and set `TOLLGATE_ROUTER_ID`. The private inventory file is ignored by git.

```bash
cp config/routers.example.json config/routers.json
TOLLGATE_ROUTER_ID=lab-router-a ./scripts/run-tests.sh <tollgate-commit>
```

## Safety Notes

- `TOLLGATE_SYSUPGRADE_WIPE_CONFIG=true` will wipe all router config (SSH keys, network, LuCI). Only use when you have physical recovery access.
- `scripts/flash-routers.mjs` writes firmware over ethernet. Ensure the correct interface is specified.
- `scripts/uboot-recover.py` involves power cycling and firmware flashing. Follow the voice/prompt instructions carefully.
- Test results may contain router IPs, passwords, and phone serials. Always run `sanitize-results.sh` before publishing.
- The `credentials/` directory contains router passwords and is gitignored. Never commit it.
