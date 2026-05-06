# physical-router-test-automation

Test automation for [tollgate-module-basic-go](https://github.com/OpenTollGate/tollgate-module-basic-go) running against a physical OpenWrt router.

Two test suites run side by side:

| Suite | Runner | What it tests | Tests |
|-------|--------|--------------|-------|
| **LuCI Admin UI** | Playwright | Dashboard, network, config, wallet fund/drain | 35 |
| **API + Phone** | pytest | Payment protocol, sessions, captive portal, metering | 51 |

These tests cannot run in GitHub CI — they require a real router on the local network.

## Prerequisites

- macOS or Linux host on the same LAN as the router
- Python 3.12+ (for pytest and cashu CLI)
- Node.js 18+ (for Playwright)
- `sshpass` (`brew install hudochenkov/sshpass/sshpass` or `apt install sshpass`)

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
TOLLGATE_LUCI_PASSWORD=<password> ./scripts/deploy.sh <git-hash>
```

Takes a branch name, tag, or commit hash.

## Run Tests

### pytest (API + Phone)

```bash
# Quick sanity check (~2s, API-only)
./scripts/run-api.sh -m smoke

# Core API tests (~40s)
./scripts/run-api.sh

# Phone tests (requires Android via ADB)
./scripts/run-phone.sh

# API + Phone + Playwright (everything)
./scripts/run-all.sh

# Run from macOS instead of ADB (no phone needed)
./scripts/run-api.sh --client=mac

# Run from Linux instead of ADB (no phone needed)
./scripts/run-api.sh --client=linux

# Publish mode — only screenshots from @pytest.mark.publish_screenshot tests
./scripts/run-api.sh --publish
```

Runner scripts capture results to `results/<timestamp>-<sha>/raw/` with HTML report, JUnit XML, and console output.

### Playwright (LuCI Admin UI)

```bash
TOLLGATE_LUCI_PASSWORD=<password> ./scripts/run-tests.sh [desktop|mobile]
```

Defaults to `desktop` viewport. Runs ~3 minutes with 35 tests.

### Test Markers

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
| `publish_screenshot` | Screenshot is safe to include in published reports | — |

Tier hierarchy: `smoke ⊂ critical ⊂ extended`. Running `-m critical` includes all smoke tests; `-m extended` includes all tests.

### Client Modes

| Flag | WiFi client | Phone tests | Notes |
|------|------------|-------------|-------|
| `--client=adb` (default) | Android phone via ADB | All 51 tests | Requires PHONE_SERIAL in .env |
| `--client=mac` | macOS via networksetup | 45 tests (1 android_only skipped) | Auto-detects WiFi MAC and IP |
| `--client=linux` | Linux via NetworkManager/nmcli | 45 tests (1 android_only skipped) | Auto-detects WiFi MAC and IP |

### pytest Flags

| Flag | Purpose |
|------|---------|
| `--client=adb|mac|linux` | WiFi client mode |
| `--publish` | Only include `@pytest.mark.publish_screenshot` screenshots in report |
| `--binary <path.ipk>` | Install .ipk on router before tests |
| `--restore` | Restore previous binary after tests |
| `--results <path>` | Custom results directory |
| `--no-deploy` | Skip portal deploy before phone tests |

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TOLLGATE_LUCI_PASSWORD` | Yes | — | Router SSH and LuCI password |
| `TOLLGATE_SSH_HOST` | No | `192.168.13.112` | Router IP for SSH |
| `TOLLGATE_LUCI_URL` | No | `http://<host>:8080` | LuCI admin URL |
| `TOLLGATE_LUCI_USER` | No | `root` | LuCI/SSH username |
| `TOLLGATE_SSID` | No | `TollGate` | WiFi SSID (auto-detected if prefix matches) |
| `TOLLGATE_DOMAIN` | No | — | Portal domain (e.g., `tollgate.local`) |
| `PHONE_SERIAL` | For ADB mode | — | Android device serial for ADB |
| `PHONE_PIN` | No | — | Android lock screen PIN |
| `TOLLGATE_CLIENT_IP` | No | Auto-detected | Client IP on the TollGate AP |
| `TOLLGATE_CLIENT_MAC` | No | Auto-detected | Client MAC on the TollGate AP |
| `TOLLGATE_PYTHON_VENV` | No | `~/.tollgate-test-venv` | Python venv path |
| `TOLLGATE_CASHU_VENV` | No | `/tmp/cashu-venv` | Cashu CLI venv path |
| `TOLLGATE_VIEWPORT` | No | `desktop` | Playwright viewport: `desktop` or `mobile` |

## Test Categories

### LuCI Admin UI (Playwright)

- **Tab loading** — all 5 tabs render without errors
- **Dashboard** — restart modal, fund warning, drain modal
- **Network** — show password, rename SSID round-trip, change password
- **Configuration** — profit share sliders, add/remove mint/share/identity, save round-trip
- **Advanced** — JSON validation, reload files, identity editor
- **Fund/Drain** — real testnut.cashu.exchange tokens, SSH file verification, lifecycle round-trips

### API Tests (pytest)

- **Health & discovery** — backend status, TIP-01 info endpoint, RFC 8908 captive portal API
- **Payment structure** — POST / response codes, NUT-24 headers, wrong mint rejection, minimum token
- **CGI endpoints** — pending token write/read/consume, notice events, log beacon, session state
- **Concurrency** — concurrent payments with same/different tokens

### Phone Tests (pytest)

- **Payment flows** — direct payment, V3/V4 token formats, paste delivery, URL param handoff
- **Session lifecycle** — short session expiry, session extension, backend restart recovery
- **Edge cases** — spent token reuse, invalid token, re-auth after expiry
- **Metering** — time-based (milliseconds) and data-based (bytes) allotment accuracy
- **Captive portal** — camera diagnostics, portal WebView behavior

## Publishing Results

Results are published to GitHub Pages after sanitization. The pipeline ensures no sensitive data (IPs, MACs, SSIDs, tokens, passwords) reaches the public report.

```bash
# 1. Run tests (results captured to results/<run-id>/raw/)
./scripts/run-api.sh --publish

# 2. Sanitize — redact all sensitive values
./scripts/sanitize-results.sh results/<run-id>/raw results/<run-id>/sanitized

# 3. Publish to GitHub Pages
./scripts/publish-report.sh <commit-hash> results/<run-id>/sanitized
```

### What gets redacted

| Data | Replacement |
|------|------------|
| Router IP + subnet | `<router-ip>`, `<client-ip>` |
| MAC addresses | `<mac>` |
| WiFi SSID | `<ssid>` |
| Phone serial | `<phone-serial>` |
| Cashu tokens | `<redacted:token>` |
| Local paths | `<local-path>` |
| Router password | `<redacted:password>` |

### Screenshots

- **Local mode** (default): all screenshots captured and embedded in the HTML report
- **Publish mode** (`--publish`): only screenshots from tests marked `@pytest.mark.publish_screenshot` are embedded in the report
- All screenshots are always saved to `raw/` (private, gitignored) regardless of mode

```python
@pytest.mark.publish_screenshot
def test_health(router):
    # This test's screenshots will appear in published reports
    ...
```

## Project Structure

```
├── lib/                          # Shared Python test infrastructure
│   ├── router.py                 # Router SSH/API/payment/session wrapper
│   ├── cashu.py                  # Cashu token minting from testnut.cashu.exchange
│   ├── helpers.py                # Payment/session lifecycle assertions
│   ├── constants.py              # Ports, token sizes, mint URL, Android constants
│   └── clients/                  # Device client adapters
│       ├── adb.py                # Android via ADB
│       ├── wifi.py               # WiFi connect/reconnect/portal detection
│       └── desktop.py            # macOS and Linux WiFi adapters
│
├── tests/
│   ├── conftest.py               # pytest fixtures, hooks, markers, result capture
│   ├── api/                      # API-only tests (35 tests, no phone needed)
│   └── phone/                    # Phone tests (16 tests, requires ADB or desktop)
│
├── scripts/
│   ├── run-api.sh                # pytest API suite runner
│   ├── run-phone.sh              # pytest phone suite runner
│   ├── run-all.sh                # All suites: Playwright + API + phone
│   ├── run-tests.sh              # Playwright LuCI runner
│   ├── sanitize-results.sh       # Redact sensitive data from test results
│   ├── publish-report.sh         # Publish sanitized results to GitHub Pages
│   ├── deploy.sh                 # Build + deploy ipk to router
│   ├── setup-python.sh           # Create Python venv with pytest deps
│   └── setup-cashu.sh            # Create cashu CLI venv
│
├── pytest.ini                    # Markers, testpaths, timeout defaults
├── requirements.txt              # Python test dependencies
├── package.json                  # Playwright deps
└── .env.example                  # Environment variable template
```

## cashu CLI Notes

The test suite uses [cashu](https://github.com/cashubtc/cashu) to mint tokens from `testnut.cashu.exchange` (a FakeWallet mint that auto-pays invoices, no real sats). The `setup-cashu.sh` script applies a one-line patch to handle a version mismatch with the testnut mint's API (missing `active` field on keysets).
