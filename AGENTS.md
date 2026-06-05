# AGENTS.md — Operational Knowledge for physical-router-test-automation

This file contains hard-won operational knowledge for agents and humans working with physical OpenWrt routers (D-Link COVR-X1860, GL.iNet GL-MT3000). Read this before touching a router.

## What This Repository Does

This is a **multi-tier test framework** for [tollgate-module-basic-go](https://github.com/OpenTollGate/tollgate-module-basic-go) (Go v1) and [tollgate-rs](https://github.com/OpenTollGate/tollgate-rs) (Rust v1) running on physical OpenWrt routers. It tests the TollGate WiFi payment system — Cashu ecash tokens for metered internet access through captive portals.

**Three test tiers:**
- **API** (`tests/api/`) — SSH to router, hit HTTP endpoints. No phone needed. Fast.
- **Phone** (`tests/phone/`) — Android device via ADB through captive portal. E2E.
- **LuCI** (`tests/web/`) — Playwright browser tests against LuCI admin UI.

**Where tests run:**
- **Physical lab** — real routers on LAN, real phones, real WiFi.
- **Cloud lab** — GCP nested-KVM (OpenWrt + Debian QEMU). API tests only, fire-and-forget.
- **Virtual lab** — local QEMU + network namespaces. For development.

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                    Test Machine (macOS/Linux)                │
│                                                              │
│  pytest ─── Router.ssh() ──── sshpass/sshkey ──────────┐   │
│       │                                                  │   │
│       ├── lib/router.py ──────────── SSH to router ─────┤   │
│       ├── lib/cashu.py ──────────── Token minting       │   │
│       ├── lib/helpers.py ────────── gate_bug_fix, etc   │   │
│       ├── lib/deploy.py ─────────── CI artifact deploy  │   │
│       └── lib/clients/adb.py ────── Phone control        │   │
│                              │                            │   │
│                              ▼                            │   │
│              ┌──────────────────────────┐                 │   │
│              │  TollGate Router (OpenWrt)│◄───────────────┘   │
│              │  - nodogsplash (captive)  │                     │
│              │  - tollgate-wrt (Go/Rust) │                     │
│              │  - Port 2121 (backend)    │                     │
│              │  - Port 2050 (NDS portal) │                     │
│              └──────────────────────────┘                     │
│                     │ LAN WiFi                               │
│                     ▼                                        │
│              ┌──────────────────┐                            │
│              │  Android Phone   │                            │
│              │  (ADB connected)  │                            │
│              └──────────────────┘                            │
└──────────────────────────────────────────────────────────────┘
```

**Key ports on the router:**
- `2121` — TollGate backend API (GET /, POST /, GET /usage, GET /balance, GET /whoami, ln-invoice)
- `2050` — Nodogsplash captive portal gateway port (CGI scripts)
- `8080` — LuCI admin UI (Go v1 only)
- `22` — SSH (WAN firewall rule required for remote access)

## Test Gating Strategies

Tests decide at runtime whether they can run against the deployed firmware. There are three strategies. **The `pr(N)` marker mechanism still exists in `conftest.py` but NO test file uses it.** All tests use one of these three approaches:

### 1. Feature Detection (skip if absent)

Tests probe the router for a specific capability. If absent, `pytest.skip()`. These tests run against any firmware that has the feature.

```python
def _skip_if_no_degraded_support(router):
    resp = router.get_tollgate_status()
    if resp.get("success") is not True:
        pytest.skip("status command not available")
    raw = json.dumps(resp).lower()
    if not any(kw in raw for kw in ["degraded", "reachable", "mint_health"]):
        pytest.skip("no degraded mode support detected")
```

Existing skip helpers in `lib/helpers.py`:
- `skip_if_no_cli_socket(router)` — checks `/var/run/tollgate.sock`
- `skip_if_no_luci(router)` — checks port 8080
- `skip_if_no_sessions_json(router)` — checks `/etc/tollgate/sessions.json`

### 2. Bug Regression via `gate_bug_fix()`

For tests that verify a **known bug has been fixed**. When the fix is absent → xfail ("known issue"). When present → runs normally. Failure = regression.

```python
from lib.helpers import gate_bug_fix

def test_profit_share_boot_with_invalid_config(router):
    gate_bug_fix(
        _has_profit_share_validation(router),
        bug_id="profit-share-no-validation",
        fix_pr="PR #86",
    )
    # test body only runs if fix is present
```

Cross-reference incidents: link to `https://github.com/OpenTollGate/tollgate-knowledgebase/tree/main/incidents/YYYY-MM-DD_slug.md` in docstrings.

### 3. Unconditional

No gating. Runs against every firmware. Use for baseline API tests (health, config format, etc.).

### Backend-Aware Skipping

Tests marked `@pytest.mark.go_only` skip when `--backend=rust`. Tests marked `@pytest.mark.rust_only` skip when `--backend=go`. This is handled in `conftest.py` `pytest_collection_modifyitems`.

`BackendConfig` (`lib/backend.py`) provides feature flags that differ between backends:
- `has_luci` — True for Go, False for Rust
- `has_cli_socket` — True for Go, False for Rust
- `has_sessions_json` — True for Go, False for Rust

| Situation | Strategy |
|---|---|
| Feature may or may not be present | Feature detection (`_skip_if_no_*`) |
| Known bug, fix in flight | `gate_bug_fix()` (xfail = warning, fail = regression) |
| Baseline behavior that always works | No gating — runs unconditionally |
| Feature only in one backend | `@pytest.mark.go_only` or `@pytest.mark.rust_only` |

## Dual-Backend Testing (Go v1 + Rust v1)

The framework supports testing both the Go and Rust TollGate backends interchangeably. Both produce the same `.ipk` package name (`tollgate-wrt`) and install to the same paths.

**Architecture reference:**

| | Go v1 | Rust v1 |
|---|---|---|
| Repo | `OpenTollGate/tollgate-module-basic-go` | `Amperstrand/tollgate-rs-ai-research-and-experiments` |
| Branch | `main` | `experimental` |
| CI workflow | "Build and Publish" | "Build and Package" |
| Config path | `/etc/tollgate/config.json` | `/etc/tollgate/config.json` |
| Service name | `tollgate-wrt` | `tollgate-wrt` |
| LuCI UI | Yes | No |
| CLI socket | `/var/run/tollgate.sock` | Not implemented |
| Session persistence | `/etc/tollgate/sessions.json` | In-memory only |
| API endpoints | 7 (all) | 7 (all) — v1 parity complete |
| Mint keyset support | V1 only (gonuts) | V1 + V2 (cdk) |

**Switching backends:**

```bash
# Deploy Rust
TOLLGATE_BACKEND=rust ./scripts/deploy-rust-ci.sh experimental '' 192.168.13.112

# Deploy Go
TOLLGATE_BACKEND=go ./scripts/deploy-ci.sh main '' 192.168.13.112

# Run Rust tests
TOLLGATE_BACKEND=rust make pytest-smoke-rust

# Run Go tests
TOLLGATE_BACKEND=go make pytest-smoke
```

Full Rust testing guide: `docs/testing-rust-v1.md`.

## Make-to-Pytest Migration Status

**Hardware lab tests are pytest-first.** Root Makefile targets (`make smoke-degraded`, etc.) are thin stubs that invoke `scripts/pymake.py`. The mapping lives in `config/make-pytest-map.yaml`.

- **44 targets registered** in the live registry
- **40 covered** by pytest/Playwright
- **4 ops-only** (serial shell/status/recovery, arch-test-full delegation)
- **Legacy `mint-health/Makefile`** (~3000 lines) remains for unmigrated targets

The migration is effectively complete for router hardware tests. See `docs/make-to-pytest-migration.md` for the full coverage matrix.

## Key Library Modules

### `lib/router.py` — Router SSH Interaction

The `Router` class is the primary interface to the router. Key methods:

| Method | What it does |
|---|---|
| `ssh(cmd)` | Execute command via SSH, return stdout |
| `scp_to(local, remote)` | Copy file to router (uses `scp -O`) |
| `write_remote_text(path, content)` | Write text file on router via stdin |
| `write_remote_json(path, payload)` | Write JSON file on router |
| `backend_url(path)` | Build URL `http://[::1]:2121{path}` |
| `api_status(path)` | HTTP GET to backend, return status code |
| `pay_direct(token)` | POST Cashu token to `http://[::1]:2121/` |
| `get_session(ip)` | GET `/usage` or `/balance` for client |
| `get_nds_state(mac)` | `ndsctl status` — Authenticated/Preauthenticated/etc |
| `get_tollgate_status()` | `tollgate status` via CLI socket (Go only) |
| `reset_state(mac, adb)` | Deauth client, reset NDS, clear logs |
| `ensure_test_mint()` | Verify `testnut.cashu.exchange` is in config |
| `replace_mints(urls)` | Replace mint URLs in config and restart |
| `fix_nodogsplash_dhcp()` | Patch ndsRTR chain to allow DHCP through |
| `disable_ipv6_on_lan()` | Prevent captive portal bypass via IPv6 RA |

**SSH connection**: Uses control master (`ControlPersist=60`) for reuse. Supports password auth (`sshpass`) and key auth. Jump hosts (`-J`) for virtual lab and offline routers.

### `lib/helpers.py` — Shared Test Utilities

| Function | Purpose |
|---|---|
| `gate_bug_fix(fix_present, *, bug_id, fix_pr)` | Bug regression gate (xfail when fix absent) |
| `pay_and_wait(router, adb, token)` | Pay + wait for auth + verify |
| `assert_session_active(router, ip)` | Assert client has active session |
| `assert_deauthenticated(router, mac)` | Assert client is disconnected |
| `skip_if_no_cli_socket(router)` | Skip if no `/var/run/tollgate.sock` |
| `skip_if_no_luci(router)` | Skip if no LuCI on port 8080 |
| `skip_if_no_sessions_json(router)` | Skip if no persistent sessions |
| `require_client_identity(router)` | Skip if no client IP/MAC |

### `lib/cashu.py` — Token Minting

Wraps the `cashu` CLI for testnet token operations against `testnut.cashu.exchange` (FakeWallet mint that auto-pays invoices). The `setup-cashu.sh` script patches cashu's `models.py` for a version mismatch (missing `active` field on keysets).

### `lib/deploy.py` — CI Artifact Deployment

Downloads `.ipk` from GitHub Actions and deploys to router. Auto-detects architecture via `opkg print-architecture`. Handles factory reset, service restart, and health verification. Backend-aware — uses `BackendConfig` to select the correct repo/workflow.

### `lib/backend.py` — Backend Configuration

`BackendConfig` encapsulates Go vs Rust differences:

```python
backend = BackendConfig("rust")
backend.repo        # → "Amperstrand/tollgate-rs-ai-research-and-experiments"
backend.workflow    # → "Build and Package"
backend.has_luci    # → False
backend.has_cli_socket  # → False
```

## conftest.py Fixtures

Session-scoped (created once per test run):

| Fixture | Purpose |
|---|---|
| `router` | SSH connection to TollGate router (`Router` instance) |
| `secondary_router` | Optional second router for two-router tests |
| `adb` | Phone control — ADB, MacWiFiClient, LinuxWiFiClient, or ContainerClient |
| `cashu` | `CashuMint` helper for minting/burning testnet tokens |
| `deploy_session` | Auto-deploy (autouse) — deploys branch/binary before tests |
| `results_dir` | Canonical results directory under `results/` |
| `backend` | `BackendConfig` from `--backend` flag or env |

Per-test fixtures: `connected_wifi`, `test_pricing`, `screenshot_portal`, `screenshot_raw`.

**Auto-deploy behavior** (`deploy_session`, autouse): If `--tollgate-branch` or `--binary` is specified, the fixture deploys before tests run. Skips for unit tests and `--no-deploy`.

## pytest Configuration

Defined in `pytest.ini`. Key markers:

| Marker | Description |
|---|---|
| `smoke` | Quick sanity check (~15s) |
| `critical` | Core functionality (~2min) |
| `extended` | Full suite including edge cases (~10min) |
| `api` | No phone needed |
| `phone` | Requires ADB device |
| `config` | Modifies router config (pricing/metric) |
| `slow` | >30s |
| `go_only` | Skip when `--backend=rust` |
| `rust_only` | Skip when `--backend=go` |
| `hardware` | Physical router scenario, skipped in virtual lab |
| `destructive` | Mutates state beyond normal config toggles |

**Tier hierarchy**: `smoke` ⊂ `critical` ⊂ `extended`. Phone tests auto-get `flaky(reruns=1)` and `timeout(300s)`.

**Timeouts**: Default 60s per test. Phone tests 300s. Hardware scenarios up to 1500s.

## Environment Detection

The framework runs in three environments. Detection is automatic — no manual configuration needed.

### How it works

```
┌─────────────────────────────────────────────────────────────┐
│ Environment Detection Chain                                  │
│                                                              │
│  TOLLGATE_LAB_TYPE env var set?                              │
│       │                                                      │
│       ├── "gcloud"     → Cloud lab (GCP nested-KVM)         │
│       ├── "virtual-lab" → Virtual lab (local QEMU)          │
│       ├── "physical"   → Physical lab (real routers)        │
│       │                                                      │
│       └── (not set)                                          │
│            │                                                 │
│            ├── TOLLGATE_VIRTUAL_LAB=1? → "virtual-lab"      │
│            └── (default) → "physical"                        │
│                                                              │
│  Cloud lab worker sets BOTH:                                 │
│    TOLLGATE_VIRTUAL_LAB=1  (legacy compatibility)           │
│    TOLLGATE_LAB_TYPE=gcloud (explicit)                       │
│                                                              │
│  Virtual lab (scripts/virtual-lab.py) sets:                  │
│    TOLLGATE_VIRTUAL_LAB=1                                    │
│    TOLLGATE_SSH_JUMP_HOST=<jump-host>                        │
│                                                              │
│  Physical lab: neither set → defaults to "physical"          │
└─────────────────────────────────────────────────────────────┘
```

### Markers that gate on environment

| Marker | Effect |
|--------|--------|
| `virtual_lab` | Only runs when `TOLLGATE_VIRTUAL_LAB=1` (cloud + local virtual lab) |
| `virtual_lab_only` | Only runs when `TOLLGATE_LAB_TYPE == "virtual-lab"` (NOT gcloud) |
| `gcloud_only` | Only runs when `TOLLGATE_LAB_TYPE == "gcloud"` |
| `physical_only` | Only runs when `TOLLGATE_LAB_TYPE == "physical"` |
| `physical_hardware` | Requires physical router/radio behavior |
| `hardware` | Auto-added to `tests/scenarios/*`; skipped in virtual lab unless also `virtual_lab` |
| `requires_wifi` | Skipped with `--client=container` (no WiFi adapter) |
| `android_only` | Skipped without physical Android device |

### Per-environment behavior

| Feature | Physical Lab | Virtual Lab | Cloud Lab |
|---------|-------------|-------------|-----------|
| Router access | SSH direct | SSH via jump host | SSH to QEMU VM |
| Client type | ADB / Mac / Linux | Container (Debian QEMU) | Container (Debian QEMU) |
| WiFi | Real radios | None by default | hwsim (opt-in with `--hwsim`) |
| Phone tests | Yes (ADB) | No | No |
| LuCI Playwright | Yes | No | No |
| Local mints | Public testnut | Optional | 3 local mints (CDK + Nutshell) |
| Mints | `testnut.cashu.exchange` | Configurable | Auto-selected (V2 probe → V1 fallback) |
| Auto-deploy | Manual / `--tollgate-branch` | Manual | CI artifact (auto) |
| Results publish | Manual `--publish` | Manual | `--publish` (fire-and-forget) |

### Environment-specific env vars

**All environments** read `TOLLGATE_SSH_HOST`, `TOLLGATE_SSH_PASSWORD`, `TOLLGATE_LUCI_PASSWORD`, `TOLLGATE_BACKEND`.

**Cloud lab worker** writes to `.env` (via `write_env_file()`):
- `TOLLGATE_VIRTUAL_LAB=1`, `TOLLGATE_LAB_TYPE=gcloud`
- `TOLLGATE_CLIENT_TYPE=container`, `TOLLGATE_CONTAINER_HOST=10.99.99.100`
- `TOLLGATE_SSH_HOST=10.99.99.1`, `TOLLGATE_CASHU_VENV=/opt/cashu-venv`
- `TOLLGATE_ENABLE_HWSIM=1` (only when `--hwsim` flag passed)
- `TOLLGATE_ENABLE_RESELLER_SCENARIOS=1` (only when `--reseller-scenarios` passed)

**Virtual lab** (`scripts/virtual-lab.py start-poc`) sets:
- `TOLLGATE_VIRTUAL_LAB=1`, `TOLLGATE_SSH_JUMP_HOST`
- `TOLLGATE_CLIENT_TYPE=container`

**Physical lab**: just `TOLLGATE_SSH_HOST` + `TOLLGATE_LUCI_PASSWORD`.

## Lessons Learned

### `chpasswd` does not exist on OpenWrt BusyBox

OpenWrt's BusyBox does not ship with `chpasswd`. Attempting `echo 'root:pw' | chpasswd` in a uci-defaults script will fail. Use `printf '%s\n%s\n' 'pw' 'pw' | passwd root` instead.

**Impact**: When this failed in our uci-defaults script with `set -eu`, the entire script aborted. The SSH key was written (it came before chpasswd) but the WAN firewall rule never got added. The router was unreachable from WAN until we connected via LAN.

### Never use `set -eu` in uci-defaults scripts

uci-defaults scripts run once on first boot. If you use `set -eu`, any single command failure aborts the entire script. Later commands (like firewall rules) never execute. This can leave the router in an unreachable state.

Instead: let each command run independently. If a command might fail, handle it explicitly. The `exit 0` at the end is what tells OpenWrt to delete the script — it must always be reached.

### SCP requires `-O` flag for OpenWrt

OpenWrt's BusyBox does not include `sftp-server`. Modern OpenSSH defaults to SFTP for SCP transfers, which fails with `ash: /usr/libexec/sftp-server: not found`. Always use `scp -O` (legacy SCP protocol) when copying files to OpenWrt routers.

### The ASU API blocks Python's default User-Agent

The OpenWrt ASU (Attended Sysupgrade) server at `sysupgrade.openwrt.org` returns HTTP 403 for requests with the default `Python-urllib/3.x` User-Agent. Set a custom `User-Agent` header (e.g., `tollgate-build-firmware/1.0`) on all requests to the ASU API.

### Don't accidentally modify the main router

When testing router access from a machine that also has SSH access to the upstream/main router, be extremely careful with IP addresses. Commands like `passwd` or `chpasswd` run without confirmation. Always verify which host you're SSH'd into before running destructive commands.

### Go wallet (gonuts) vs CDK Keyset ID V1/V2 incompatibility

The TollGate Go backend uses `gonuts` which only supports Keyset ID V1 (`00`-prefix, 8 bytes, e.g. `0016f5fb5e5278f2`). CDK 0.16.0+ generates Keyset ID V2 (`01`-prefix, 33 bytes, e.g. `01df97b6fb8a572a718d7df7fcbf4387e2d455134ea8004c9c8c51e1b3391f909e`).

Configuring the Go backend with a CDK mint causes a FATAL crash on startup: `"error adding new mint: Got invalid keyset. Derived id: '0016f5fb5e5278f2' but got '01df97b6...' from mint"`. The router's `/etc/tollgate/config.json` must use `testnut.cashu.exchange` (V1 keysets), NOT the local CDK mint.

The local CDK mint (port 8085) works fine with the Python `cashu` CLI. It just can't be the Go backend's configured mint. This is tracked as GitHub issue #18.

V2 spec (NUT-02 PR #182, merged Jan 2026): `01` + SHA256(`amount:pubkey_hex` pairs sorted, comma-separated, `|unit:sat`). V1: `00` + first 14 hex chars of SHA256(concat of raw pubkeys).

**Fix path**: `Amperstrand/gonuts-tollgate` fork at `feature/v2-keyset-ids` branch adds `DeriveKeysetIdV2()` and `IsKeysetIdV2()` following NUT-02. The fix updates `wallet/keyset.go:GetKeysetKeys()` to use V2 derivation when the keyset ID starts with `01`. To apply: update `src/tollwallet/go.mod` in `tollgate-module-basic-go` to pin `github.com/Amperstrand/gonuts-tollgate` at the V2 branch, then rebuild the `.ipk`.

### Nodogsplash DHCP bypass required

Nodogsplash's `ndsRTR` iptables chain drops ALL unauthenticated packets (mark 0x10000) at rule 1, which silently kills DHCP DISCOVER from clients. Without the bypass fix (in `Router.fix_nodogsplash_dhcp()`), phones can associate at L2 but never get an IP — Android shows "Connection failed" and auto-reconnects to a known-good network.

### IPv6 captive portal bypass

Nodogsplash only manages IPv4 iptables. If IPv6 Router Advertisements are active on LAN, WiFi clients get global IPv6 addresses and Android validates connectivity over IPv6, completely bypassing the captive portal. `Router.disable_ipv6_on_lan()` disables RA, DHCPv6, and removes the LAN IPv6 prefix.

### Offline router deployment (no internet, no opkg update)

When a router has no internet (e.g., downstream/reseller behind a jump host), `opkg update` and `opkg install` will fail. You must manually SCP all packages and their dependencies.

**TollGate's declared dependencies** (from the Makefile `DEPENDS`):
- `nodogsplash`, `luci`, `jq`, `px5g-mbedtls`

**Test framework dependencies** (from `lib/deploy.py` `TEST_DEPS`):
- `curl`, `socat`, `nodogsplash`, `jq`, `luci`, `px5g-mbedtls`

Combined, these require ~52 packages (including transitive deps like `iptables-nft`, `kmod-*`, `rpcd`, `uhttpd`, `libluciheader0`, etc.).

**Procedure:**

1. **Get the package diff** — compare a fresh OpenWrt install against a router that has TollGate + deps installed:
   ```bash
   # Fresh router (offline)
   ssh -J <jump-host> root@<offline-router> "opkg list-installed" | sort > /tmp/fresh-pkgs.txt
   # Router with internet + TollGate
   ssh root@<online-router> "opkg list-installed" | sort > /tmp/full-pkgs.txt
   # Diff
   comm -13 /tmp/fresh-pkgs.txt /tmp/full-pkgs.txt | grep -v "tollgate-wrt" | awk '{print $1}'
   ```

2. **Download all deps on the online router:**
   ```bash
   ssh root@<online-router> "mkdir -p /tmp/deps && cd /tmp/deps && \
     for pkg in <package-list>; do opkg download \$pkg; done"
   ssh root@<online-router> "cd /tmp/deps && tar czf /tmp/tollgate-deps.tar.gz *.ipk"
   ```

3. **Relay to Mac, then to offline router through jump host:**
   ```bash
   scp -O root@<online-router>:/tmp/tollgate-deps.tar.gz /tmp/tollgate-deps.tar.gz
   scp -O -J <jump-host> /tmp/tollgate-deps.tar.gz /tmp/tollgate-build/tollgate-wrt-*.ipk \
     root@<offline-router>:/tmp/
   ```

4. **Install on offline router:**
   ```bash
   ssh -J <jump-host> root@<offline-router> "
     cd /tmp && mkdir -p deps && cd deps && tar xzf ../tollgate-deps.tar.gz
     opkg install /tmp/deps/*.ipk
     opkg install --force-overwrite /tmp/tollgate-wrt-*.ipk
   "
   ```

**Note:** Always use `scp -O` for OpenWrt (no sftp-server). The total dependency bundle for mipsel_24kc is ~1.8MB. The TollGate ipk itself is ~6.3MB.

### gonuts-tollgate bolt11 decode tolerance (FakeWallet mints)

**Status**: Hacky but working. Lightning payments confirmed with testnut.cashu.exchange.

**The problem**: FakeWallet mints (testnut.cashu.exchange) return non-standard "invoice" strings in their NUT-04 mint quote responses — not valid bolt11 invoices. gonuts-tollgate's `Wallet.RequestMint()` called `decodepay.Decodepay()` on the response and treated any decode failure as fatal, rejecting the entire mint quote. This blocked all Lightning payments against testnut.

**The fix** (in `Amperstrand/gonuts-tollgate` `feature/v2-keyset-ids`, commit `9b2b843`):
```go
// Before (fatal):
bolt11, err := decodepay.Decodepay(mintResponse.Request)
if err != nil {
    return nil, fmt.Errorf("error decoding bolt11 invoice: %v", err)
}
quote := storage.MintQuote{..., CreatedAt: int64(bolt11.CreatedAt)}

// After (tolerant):
createdAt := time.Now().Unix()
if bolt11, err := decodepay.Decodepay(mintResponse.Request); err == nil {
    createdAt = int64(bolt11.CreatedAt)
}
quote := storage.MintQuote{..., CreatedAt: createdAt}
```

`decodepay.Decodepay()` is only used to extract `CreatedAt` from the invoice. When the mint returns garbage, we fall back to `time.Now()`. The quote is still stored, monitoring still works, tokens still get minted when paid — the entire Lightning flow works.

**Why it couldn't be fixed at a higher layer**: The error occurs inside gonuts's `RequestMint()` before the quote is stored. The backend's `merchant/lightning.go` calls `tollwallet.RequestMintQuote()` which calls `wallet.RequestMint()`. If `RequestMint()` fails, no quote exists in gonuts's internal DB, so `MintQuoteState()` and `MintTokens()` can't find it later. The monitoring goroutine in `merchant/lightning.go` depends on gonuts having the quote. Duplicating this logic in the backend would require rearchitecting the wallet layer.

**Portal-side fix** (in `net4sats-captive-portal`): Added `LN005` error code that detects bolt11/zpay32 errors and shows "Lightning payments are not available with this mint. Please use Cashu tokens instead." This is a fallback for any remaining edge cases.

**Deployment regression**: PR #126 was merged as `2cb771f` but the merge commit resolved the gonuts version conflict to the **tagged `v0.7.0` release**, which does NOT include the bolt11 tolerance fix. The `v0.7.0` tag was created from gonuts `main` before the `9b2b843` commit was merged. The fix only exists on the `feature/v2-keyset-ids` branch as pseudo-version `v0.0.0-20260528233401-9b2b84344c3a`.

**Tracked as**: https://github.com/OpenTollGate/tollgate-module-basic-go/issues/156

**Workaround**: `scripts/patch-gonuts-version.sh` is called by `deploy.sh` after cloning. It sed-replaces `v0.7.0` → `v0.0.0-20260528233401-9b2b84344c3a` in all three `go.mod` files before building. **Remove this script and its call in `deploy.sh` when gonuts-tollgate includes the bolt11 fix in a tagged release and tollgate-module-basic-go updates to it.**

### Lightning invoice flow and testnut bolt11 limitation

**The `/ln-invoice` endpoint** was added to tollgate-module-basic-go for Lightning payment support through the captive portal. The flow:

1. Portal calls `POST /ln-invoice` with `{amount, mint_url}` → `main.go:handleLightningInvoicePost()`
2. Backend calls `Merchant.RequestLightningInvoice(mac, mintURL, amount)` → `merchant/lightning.go`
3. Calls `tollwallet.RequestMintQuote(amount, mintURL)` → gonuts `RequestMint()` → NUT-04 on mint
4. Mint returns `{request: "...", quote: "...", state: UNPAID}`
5. Backend stores the quote, starts `monitorLightningQuote()` goroutine
6. Returns `{invoice: request, quote, amount, expiry}` to portal
7. Portal decodes the invoice as bolt11 for QR code display
8. Monitoring goroutine polls `MintQuoteState()` every 2s, detects payment, mints tokens, grants access

**testnut.cashu.exchange returns a dummy string, not bolt11**:

```
testnut.cashu.exchange → "dummy-mint-4-46876457c0684c65d07e993705706d7b84c528aa75be1c722b8970f37585c7ba-exp1780177644"
testnut.cashu.space    → "lnbc40n1p4pkkhl9qypqqqdqqxqrrsssp5recjpm6q6q8dnjqh04gpqn5zt8hfrhrtkdwtpwynnenjnjtm5hkq..."
```

`testnut.cashu.exchange` returns a placeholder string (`dummy-mint-{amount}-{hash}-exp{timestamp}`) that is NOT a bolt11 invoice. `testnut.cashu.space` returns a proper signed bolt11 invoice. Both CDK FakeWallet and Nutshell FakeWallet have proper `InvoiceBuilder` implementations — the `.exchange` domain appears to run a different or customized version.

**Impact on testing**:
- The gonuts tolerant fix (above) allows the quote to be created and stored despite the invalid `request`
- The monitoring goroutine works correctly — FakeWallet auto-pays, tokens get minted, access is granted
- The portal cannot decode the dummy string as bolt11 → Lightning tab shows error
- Cashu token payment flow is unaffected
- This is ONLY an issue with `testnut.cashu.exchange`. Production mints and `testnut.cashu.space` return valid bolt11

**Workaround for Lightning simulation with testnut**: Either switch to `testnut.cashu.space` (returns real bolt11), or add invoice validation in `merchant/lightning.go:RequestLightningInvoice()` that detects non-bolt11 and returns an empty invoice string, letting the portal enter "auto-pay polling" mode instead of trying to display a QR code.

**The "amount 0 sats" log error is NOT from `/ln-invoice`**: The backend log message `"Error getting invoice: amount 0 sats is outside allowed range (1000-1000000000 msats)"` comes from `MeltToLightning()` → `GetInvoiceFromLightningAddress()` in the **profit-share payout routine**, which runs on a 60-second timer. It fires when `aimedPaymentAmount * factor` rounds to 0 via integer math. This is a separate issue (filed as physical-router-test-automation#25) — the fix is a zero-amount guard in `processPayout()` before calling `MeltToLightning`. Do not confuse this error with the `/ln-invoice` flow.

### TollGate init script uses `/usr/bin/`, not `/usr/sbin/`

The tollgate-wrt init script (`/etc/init.d/tollgate-wrt`) runs the binary from `/usr/bin/tollgate-wrt`, NOT `/usr/sbin/tollgate-wrt`. If you SCP a custom binary, make sure you copy it to the correct path:

```bash
# CORRECT
scp -O tollgate-wrt root@router:/tmp/tollgate-wrt
ssh root@router "cp /tmp/tollgate-wrt /usr/bin/tollgate-wrt && /etc/init.d/tollgate-wrt restart"

# WRONG — binary won't be picked up by the service
scp -O tollgate-wrt root@router:/usr/sbin/tollgate-wrt
```

The opkg package installs to `/usr/bin/tollgate-wrt`. The `/usr/sbin/` path may have a stale copy from a previous manual deploy.

## Router Access Patterns

### GL-MT3000 Default IPs

| Mode | IP | Access |
|---|---|---|
| OpenWrt factory defaults (LAN) | 192.168.1.1 | SSH (no password), HTTP (LuCI if installed) |
| GL.iNet stock firmware (LAN) | 192.168.8.1 | HTTP admin panel |
| WAN (DHCP from upstream) | Assigned by DHCP | SSH only if WAN firewall rule exists |
| U-Boot recovery | 192.168.1.1 | HTTP only (web UI for firmware upload) |

### SSH Authentication

The test framework uses `sshpass` (password auth) via `TOLLGATE_LUCI_PASSWORD`. Custom firmware images built with `scripts/build-firmware.py` embed both an SSH key and a random password. Both methods work.

### Network Topology

```
Internet → Main Router (192.168.13.1) → Switch → TollGate Router WAN (192.168.13.112)
                                                ↕
                                           Test Machine (192.168.13.244)
```

When connected directly to the TollGate router's LAN port, the test machine gets an IP in 192.168.1.0/24 (en6 on current setup).

## Primary Test Workflow

**PR / CI (no physical hardware):**

```bash
./scripts/test-pr.sh --pr <N> [--reset] [--test api|all] [--publish]
```

**Physical lab (pytest-first, Make stubs forward to pymake):**

```bash
make lock PHASE='smoke-degraded'
./scripts/pymake.py smoke-degraded --router alpha
# equivalent: make smoke-degraded ROUTER=alpha
make pytest-scenarios   # all tests/scenarios/ with -m hardware
```

Registry: `config/make-pytest-map.yaml`. Migration notes: `docs/make-to-pytest-migration.md`.

`test-pr.sh` resolves the PR to a branch/commit, deploys to the router, runs tests, and generates reports. See README.md for full usage.

## Firmware Build + Flash Workflow

### Build

```bash
scripts/build-firmware.py --router lab-router-a
```

Reads `config/routers.json` for target/profile/version. Auto-detects SSH key from `~/.ssh/`. Generates random password. Builds via ASU API. Downloads sysupgrade image. Saves credentials to `credentials/<router-id>.json`.

### Flash via SSH (LAN or WAN)

```bash
scripts/build-firmware.py --router lab-router-a --flash
```

Or manually:

```bash
scp -O <image.bin> root@<router-ip>:/tmp/
ssh root@<router-ip> "sysupgrade -n /tmp/<image.bin>"
```

`sysupgrade -n` wipes all config. The router reboots. SSH connection dies with exit code 246 (expected).

### Flash via U-Boot (recovery)

For bricked routers that won't boot properly. See the U-Boot section below.

## U-Boot Recovery

### Entering U-Boot Mode (GL-MT3000)

1. Disconnect power from router
2. Connect computer to router's **LAN port** via Ethernet (leave WAN disconnected)
3. Set computer IP to 192.168.1.x (e.g., 192.168.1.2, subnet 255.255.255.0)
4. **Press and hold the Reset button**
5. **While holding Reset, apply power**
6. Watch the LED: blue flashes ~6 times, then turns **solid white**
7. **Release Reset** when LED is solid white
8. U-Boot web UI is now at `http://192.168.1.1`

### Headless Upload via curl

```bash
curl -X POST -F gl_firmware=@<firmware.bin> http://192.168.1.1/index.html
```

The form field name is `gl_firmware`. Wait ~3 minutes. Don't power off. The router reboots automatically.

### Automated Recovery Script

```bash
scripts/uboot-recover.py --image <firmware.bin> [--interface en6]
```

Uses macOS `say` command for voice guidance. Auto-detects U-Boot mode via ping. Uploads firmware via curl. Monitors for reboot completion.

### Browser-Based Recovery (fallback)

Use Chrome or Edge (NOT Firefox — may brick the router). Visit `http://192.168.1.1` in U-Boot mode. Upload firmware via the web form. Wait ~3 minutes.

## Common Recovery Scenarios

### Router boots but no SSH from WAN

Likely: WAN firewall rule missing. Connect via LAN (192.168.1.1) and add the rule:

```bash
uci add firewall rule
uci set firewall.@rule[-1].name='Allow-SSH-WAN'
uci set firewall.@rule[-1].src='wan'
uci set firewall.@rule[-1].dest_port='22'
uci set firewall.@rule[-1].proto='tcp'
uci set firewall.@rule[-1].target='ACCEPT'
uci commit firewall
fw4 restart
```

### uci-defaults script didn't run / partially ran

Check if the script still exists:

```bash
ls /etc/uci-defaults/
```

If `99-asu-defaults` is still there, it failed partway through. Read it, fix the issue, run the remaining commands manually, then delete it.

### Router not getting WAN IP

After `sysupgrade -n`, the WAN port is configured for DHCP by default. Check that the upstream network is providing DHCP. Verify with `ping 192.168.13.1` from the router.

## GCP cloud lab (fire-and-forget)

### Cost Policy

**Always use the cheapest machine types and regions.** Our workload is light (~2.5GB RAM total for QEMU VMs). Do not use expensive machine types (c2, custom, anything >2 vCPU) without explicit user approval.

| Machine Type | $/hr | RAM | CPU Platform | Notes |
|---|---|---|---|---|
| `n1-standard-2` | $0.0950 | 7.5 GB | Intel Skylake | **Default. Cheapest.** |
| `n2-standard-2` | $0.0971 | 8 GB | Intel Cascade Lake | Fallback. 2% more. |

**Forbidden without user approval:** `c2-standard-4` ($0.1942/hr, 2x price), `n2-standard-4` ($0.1942/hr), any E2/N2D/T2A (no nested virt).

**Region priority (cheapest first):**

| Tier | Regions | $/hr (N1) | Use When |
|---|---|---|---|
| Tier 1 (cheapest) | `us-central1`, `us-east1`, `us-east5`, `us-west1` | $0.0950 | Default |
| Tier 2 (+10%) | `northamerica-northeast1/2`, `europe-west1`, `europe-west4` | $0.1045 | Tier 1 exhausted |

The fallback logic in `_create_vm_with_fallback()` tries all Tier 1 zones, then Tier 2, then alternates machine type (N1 → N2). **Never fall back to regions >20% more expensive** without asking the user first.

`scripts/cloud-lab.py submit` runs TollGate tests in nested KVM on a GCP VM (`n1-standard-2` + the `SNAPSHOT_NAME` configured in `lib/cloud_lab/constants.py`). The current snapshot is `tollgate-runner-baked-v10`; newer baked snapshots must be verified before becoming the default.

### Architecture

```
┌─ GCP Host VM (n1-standard-2, nested KVM) ──────────────────────┐
│                                                                  │
│  tg-poc-br (10.99.99.0/24) — test LAN                           │
│    ├── host: 10.99.99.2 (mints, NAT, orchestration)            │
│    ├── alpha: 10.99.99.1 (OpenWrt QEMU, TollGate under test)   │
│    └── debian: 10.99.99.100 (Debian QEMU, Playwright, cashu)   │
│                                                                  │
│  tg-beta-br (10.99.96.0/24) — isolated Beta LAN (2-router)     │
│    ├── host: 10.99.96.2 (routes to mint at 10.99.99.2)         │
│    └── beta: 10.99.96.11 (upstream TollGate merchant)           │
│                                                                  │
│  tg-upstream-br (10.99.98.0/24) — simulated WAN (2-router)     │
│    ├── alpha WAN: DHCP from beta                                │
│    └── beta WAN: 10.99.98.1 (static, DHCP server)              │
│                                                                  │
│  mgmt-br (10.99.97.0/24) — management SSH                      │
│    ├── host: 10.99.97.2                                         │
│    ├── alpha: 10.99.97.1                                        │
│    ├── beta: 10.99.97.11                                        │
│    └── debian: 10.99.97.100                                     │
│                                                                  │
│  Local mints (on host):                                         │
│    ├── CDK V2:      :8383 (V2 keysets, 01-prefix)              │
│    ├── Nutshell V2:  :8384 (V2 keysets)                         │
│    └── Nutshell V1:  :8385 (V1 keysets, 00-prefix, for Go)     │
│                                                                  │
│  OpenWrt VM (alpha):                                            │
│    ├── hwsim radios: kmod-mac80211-hwsim (pre-installed in v8)  │
│    ├── wpad-basic, iw-full, iwinfo (pre-installed in v8)        │
│    ├── radio0: 2.4GHz AP (phy0-ap0, SSID=TollGate, br-lan)     │
│    ├── radio1: 5GHz AP (phy1-ap0, SSID=TollGate, br-lan)       │
│    ├── nodogsplash: manages br-lan (captive portal on :2050)    │
│    └── /etc/hosts maps mint DNS → 10.99.99.2                   │
│                                                                  │
│  Test flow (worker.py):                                         │
│    [1] Boot GCP VM from snapshot                                │
│    [2] Clone test repo, install deps (baked)                    │
│    [3] Boot OpenWrt + Debian QEMU VMs                           │
│    [4] Setup hwsim virtual WiFi (2 radios, AP on br-lan)        │
│    [5] Start 3 local mints (CDK + Nutshell V1 + V2)            │
│    [6] Deploy TollGate .ipk to OpenWrt                          │
│    [7] Select mint (CDK V2 if backend supports it, else V1)     │
│    [7.5] If --two-router: configure Beta merchant + Alpha reseller + fund wallet
│    [8] Run tests: visual → API → vl-scenarios → scenarios       │
│    [9] Collect results, publish to gh-pages, self-delete        │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Local mint selection strategy

The worker runs 3 local Cashu mints to eliminate dependency on the public `testnut.cashu.exchange`. `select_test_mint()` probes the backend:

1. **Try CDK V2** (`http://10.99.99.2:8383`) — configure backend with V2 mint URL, check if backend returns `kind 10021` with `price_per_step` tags. If yes → V2 supported, use CDK.
2. **Fall back to Nutshell V1** (`http://10.99.99.2:8385`) — retries up to 10 times (30s). Checks for V1 keyset ID (`00`-prefix, 16 chars). Go backend (`gonuts`) requires V1 keysets.
3. **Last resort: public testnut** — only if local mints are genuinely broken.

The `.env` file's `TOLLGATE_TEST_MINT_URL` is updated to the chosen mint URL so the cashu CLI in tests uses the local mint.

### Test runners

Tests run sequentially in a single shell command with a 90-minute timeout:

| Runner | Tests | Typical time |
|--------|-------|-------------|
| **visual** | Container client e2e portal payment | ~2min |
| **api** | `tests/api/` (89 tests with local mint) | ~15min |
| **vl-scenarios** | Captive portal browser, mint health, boot hygiene, upstream WiFi | ~2min |
| **scenarios** | Reseller mode (only with `--reseller-scenarios`) | ~3min |
| **two-router** | Two-router cloud + upstream payment (only with `--two-router`) | ~5min |

Each runner writes junit.xml + report.html to `raw/<runner>/`. The overall exit code is the worst of all runners.

### Flow

1. **Local (blocking):** `ensure_artifact()` waits for upstream CI to finish and expose an `x86_64` `.ipk` (never triggers new builds).
2. **GCP VM (async):** startup script clones this repo, runs `lib.cloud_lab.worker`, publishes to gh-pages, self-deletes.
3. **Publishing:** `publish-report.sh` uses non-force pushes with up to 10 pull/rebase/push retries and random 0-60s backoff so multiple cloud runs can publish concurrently.

### Secrets

| Variable | Purpose |
|----------|---------|
| `GH_TOKEN` or `GITHUB_TOKEN` | Passed to VM metadata for `gh` artifact download, gh-pages push, PR comments |
| `TOLLGATE_GCP_SSH_KEY` | SSH key for `gcloud compute ssh` / debugging (default `~/.ssh/google_compute_engine`) |

`GH_TOKEN` in instance metadata is acceptable for a private lab; prefer Secret Manager for shared projects.

### Debian overlay caching

- **Debian qcow2 overlay** (Playwright + Chromium) lives on the baked snapshot — do **not** reset it per run.
- **OpenWrt overlay** is recreated from base each run for a clean TollGate install.
- **Persistent caches** live under `/opt` (`/opt/tollgate-venv`, `/opt/cashu-venv`, `/opt/cdk-mintd`). Avoid `/tmp` for baked caches because it may be empty after boot.
- Re-bake snapshot when Debian packages, Playwright versions, gh/gcloud CLI, Python deps, cashu, CDK, or OpenWrt provisioning logic change.

### Snapshot baking

Use `scripts/bake-snapshot.py` to create a new snapshot with all deps pre-installed and the OpenWrt base image pre-provisioned (SSH, password, firewall, network already configured).

```bash
./scripts/bake-snapshot.py bake
```

What it bakes into the snapshot:
- `gh` CLI (GitHub apt repo)
- `gcloud` CLI (Google Cloud apt repo, for VM self-delete)
- `/opt/tollgate-venv` (Python venv with pytest, playwright, etc.)
- `/opt/cashu-venv` (cashu CLI with active-field patch)
- `/opt/cdk-mintd` (CDK mintd binary for local V2 mint)
- Pre-provisioned `openwrt-base.qcow2` (SSH enabled, password set, firewall rule added, network configured to 10.99.99.1)
- **WiFi packages** (v8+): `kmod-mac80211-hwsim`, `wpad-basic`, `iw-full`, `iwinfo` for virtual radio testing

The baker must run remote setup with `HOME=/root`, because the GCP startup worker also exports `HOME=/root`. If bake commands accidentally write to `/home/<ssh-user>/tollgate-virtual-lab`, the worker will read stale images from `/root/tollgate-virtual-lab`.

After baking, verify the snapshot with a throwaway cloud run or `cloud-lab.py up` before updating `SNAPSHOT_NAME` in `lib/cloud_lab/constants.py` to the new snapshot name (auto-incremented, e.g. `tollgate-runner-baked-v9`).

The worker (`lib/cloud_lab/worker.py`) detects pre-provisioned OpenWrt bases automatically — if SSH works within 15s of boot, serial provisioning is skipped. Falls back to serial provisioning for old snapshots without pre-provisioned bases.

### Commands

```bash
./scripts/cloud-lab.py submit --pr 42 --publish
./scripts/cloud-lab.py status-run --run-id <id>
./scripts/cloud-lab.py cleanup-stale   # delete RUNNING tollgate VMs >2h old
./scripts/cloud-lab.py cleanup-all      # delete ALL tollgate VMs
./scripts/bake-snapshot.py bake         # create new snapshot with deps pre-installed
```

### Timing (v9 snapshot with local mints)

| Phase | Duration | Notes |
|---|---|---|
| VM boot + startup | ~2m | GCP startup script overhead |
| gh + venv + cashu + cdk | 0s (baked) | Pre-installed in snapshot |
| Boot OpenWrt + Debian VMs | ~30s | OpenWrt SSH-first detection, no serial |
| Start local mints | ~5s | CDK + Nutshell V1 + V2, health checked |
| Deploy TollGate | ~50s | Download + install .ipk |
| Select test mint | ~5s | V2 probe, then V1 fallback |
| Run tests | ~20m | 94 tests with local mint (no timeouts) |
| Collect + publish | ~2m | Shallow clone gh-pages + push |
| **Total (single-router)** | **~30min** | |
| **Total (two-router)** | **~50min** | Extra Beta VM + serial provisioning + deploy |

### VM lifecycle and kill switch

Cloud lab VMs use a two-layer safety mechanism:

1. **Worker `MAX_WALL_SECONDS`** (7200s / 2h): The Python worker force-deletes the VM if the pipeline runs longer than 2 hours. This handles hung test suites, stuck SSH sessions, or runaway processes.

2. **Startup script kill switch** (7200s / 2h): A background `sleep 7200 && gcloud compute instances delete` process runs independently of the worker. This is the last line of defense — it kills the VM even if the Python worker crashes or hangs.

**VM retention policy**: By default, VMs stay alive after the pipeline finishes (successful or failed). The `keep_vm_on_failure` metadata flag defaults to `True`. Only when explicitly set to `"false"` or `"0"` does the worker self-delete immediately. The 2h kill switch ensures no VM runs longer than 2 hours regardless.

**Operational rule**: Do not kill VMs until you have either:
- Verified the publish step completed (check `tests.tollgate.me` or gh-pages branch)
- Examined the logs via `gcloud compute ssh <vm> --command='cat /var/log/tollgate-run.log'`

### Publish to gh-pages — known pitfalls

The publish step (`publish-report.sh`) clones the `gh-pages` branch, copies the report, and pushes. Common failure modes:

1. **Large repo clone timeout**: The repo is ~1.4GB. Without `--depth 1`, a full clone can take 3-5 minutes from a GCP VM. Always use shallow clones.
2. **`collect_and_render` failure**: If this step fails, no `report/index.html` exists, and `publish-report.sh` exits immediately with "report/index.html not found". Check the worker log for "collect_and_render failed".
3. **gh-pages push race**: Multiple concurrent VMs pushing to gh-pages can cause git conflicts. The script retries 10 times with random backoff, but the worker publish timeout (1200s) may not be enough for many retries.
4. **`gh auth setup-git` failure**: If the GitHub token is invalid or expired, git operations fail. The `|| true` suppresses the error, but subsequent git push fails.

### mac80211_hwsim virtual WiFi (experimental, opt-in)

The v9 snapshot includes WiFi simulation packages (`kmod-mac80211-hwsim`, `wpad-basic`, `iw-full`, `iwinfo`), vwifi cross-VM relay binaries (`/opt/vwifi/bin/`), and management network UCI. Virtual WiFi is **disabled by default** — enable with `--hwsim` flag on `cloud-lab.py submit`.

**Why opt-in**: hwsim creates virtual radios on the OpenWrt VM but they cannot propagate beacons between PHYs. STA scan/association tests always skip in hwsim. The AP interfaces are useful for router-side config verification only. Portal testing works over wired br-lan without hwsim.

**How to enable**:
```bash
# Cloud lab with virtual WiFi (AP verification only, no cross-VM scan)
./scripts/cloud-lab.py submit --pr 42 --hwsim --publish

# Without hwsim (default) — portal tests still work over wired br-lan
./scripts/cloud-lab.py submit --pr 42 --publish
```

**Worker-provisioned topology** (`_setup_hwsim_wifi()` in `worker.py`):

| Component | Config | Mirrors |
|-----------|--------|---------|
| radio0 | 2.4GHz AP, channel 6, HT20, SSID=TollGate-ALPHA | MT3000 radio0 (2.4GHz) |
| radio1 | 5GHz AP, channel 36, VHT80, SSID=TollGate-ALPHA | MT3000 radio1 (5GHz) |
| Interface names | `phy0-ap0`, `phy1-ap0` | OpenWrt netifd naming |
| Bridge membership | Both AP interfaces in `br-lan` | Same as real MT3000 |
| nodogsplash | Manages `br-lan` (intercepts wired + wireless) | Same as real router |

The worker creates AP interfaces **manually** via `iw phy phy0 interface add phy0-ap0 type __ap` (bypasses netifd's mac80211.sh which fails with hwsim due to HOSTAPD_START_FAILED). It then adds them to br-lan and writes UCI wireless config for SSID/metadata. Non-fatal — if hwsim fails, the cloud lab continues without WiFi.

**Test gating**: `tests/api/test_mac80211_hwsim.py` skips entirely unless `TOLLGATE_ENABLE_HWSIM=1` or `TOLLGATE_ENABLE_VWIFI=1` is set. When enabled:
- AP tests (interface existence, bridge membership, SSID) pass
- STA tests (scan, association, DHCP) skip with hwsim — PHYs cannot see each other's beacons
- STA tests **run and pass** with `--vwifi` — vwifi relays frames between VMs
- Set `HWSIM_STA_ENABLED=1` to force-run STA tests (only useful on physical hardware with real radios)

**Debian container (10.99.99.100)** connects over wired ethernet on `br-lan`. Nodogsplash intercepts ALL traffic on `br-lan` regardless of whether the client arrived via WiFi or ethernet — the captive portal flow works without cross-VM WiFi. Playwright navigates to `http://10.99.99.1:2050/` directly.

OpenWrt names hwsim interfaces `phy<N>-ap0` (not `wlan0`). Tests must check `iw dev` output for interface names, not hardcoded names.

**Known limitations**:

- **No cross-PHY beacon propagation**: hwsim PHYs exist in separate kernel namespaces and do not simulate RF propagation. radio1's STA cannot see radio0's AP beacons.
- **No cross-VM WiFi**: QEMU guests run separate kernels — hwsim radios don't share RF state across VM boundaries (use `--vwifi` to solve this).
- **STA mode is read-only config verification**: reconfigures radio1 to STA mode on a dedicated `wwan` network, but association always fails in hwsim (unless `--vwifi` is used).

### vwifi cross-VM WiFi frame relay (opt-in)

[vwifi](https://github.com/Raizo62/vwifi) relays 802.11 frames between QEMU VMs via TCP, enabling real `iw scan` from the Debian guest to see SSIDs on the OpenWrt guest. This solves the cross-kernel hwsim limitation.

**How to enable**:
```bash
./scripts/cloud-lab.py submit --pr 42 --vwifi --publish
```

**Architecture**: A `vwifi-server` runs on the GCP host (TCP port 8212). Inside each guest, `vwifi-client` connects to the host via TCP. Guests load `mac80211_hwsim radios=0` (empty), then `vwifi-add-interfaces` creates relayed wlan interfaces. OpenWrt keeps its existing baked hwsim radios (can't rmmod — netifd holds refs) and gets an additional relayed interface via `vwifi-add-interfaces`.

**Correct vwifi procedure** (from README, verified):
1. `modprobe mac80211_hwsim radios=0` (empty — no local radios)
2. `vwifi-add-interfaces 1 <mac>` (creates relayed wlan interface)
3. `vwifi-client <host_ip>` (relays frames for vwifi-created interfaces only)

Debian uses `radios=0` + `vwifi-add-interfaces` to get a clean relayed `wlan0`. OpenWrt skips `radios=0` (existing radios can't be removed) and uses `vwifi-add-interfaces` to add a relayed interface alongside the local ones, then reconfigures hostapd to broadcast `TollGate-ALPHA` on the relayed interface.

**Worker pipeline** (when `--vwifi` is passed):
1. `_setup_vwifi_host()` — starts vwifi-server on host (TCP mode, port 8212)
2. `start_inner_vms()` — passes `mgmt_tap`/`mgmt_mac` for management NIC
3. `_setup_hwsim_wifi(vwifi_mode=True)` — configures UCI wireless on existing hwsim radios
4. `_setup_vwifi_guests()` — SCPs binaries to both VMs, creates relayed interfaces, starts vwifi-client, runs hostapd on OpenWrt, captures iw scan proof artifacts

**Scan proof artifacts**: Worker saves `iw scan` output from both VMs to `results/raw/virtual-wifi/iw-scan-openwrt.txt` and `iw-scan-debian.txt`. These appear as clickable links in the published report under "Native Reports".

**Verified**: Debian `iw scan` sees `TollGate-ALPHA` — cross-VM 802.11 management frame relay confirmed.

**Key implementation details**:
- **TCP mode** (not vsock) — vsock has zombie process kernel bugs on some kernels
- **BusyBox `nohup`** — OpenWrt ash doesn't have `nohup`; use bare `&` for background processes
- **`ip link set wlan0 up`** — vwifi-created interfaces are DOWN by default; must bring up before scan
- **Debian `nohup + disown`** — bash keeps SSH sessions alive with bare `&`; need nohup + redirect + disown for clean detach
- **Management network** — `mgmt-br` (10.99.97.0/24) provides SSH access independent of test network bridges

**Build requirements**: `cmake make g++ pkg-config libnl-3-dev libnl-genl-3-dev`. Build script: `scripts/build-vwifi.sh` (Alpine Docker for static musl guest binaries, glibc for host). Snapshot v9 includes pre-built vwifi binaries at `/opt/vwifi/bin/`.

**CID assignments** (legacy, now TCP):
| VM | vsock CID (unused) | TCP target |
|----|-----------|----------|
| Alpha OpenWrt | 10 | `10.99.99.2:8212` |
| Debian client | 20 | `10.99.99.2:8212` |
| Beta OpenWrt (two-router) | 11 | `10.99.99.2:8212` |

### What works in the cloud lab

- API tests (89 passed, 0 failed with local mint)
- Container client e2e portal payment (visual recording)
- Scenario tests: captive portal browser, mint health, boot hygiene, upstream WiFi CLI
- Reseller mode scenarios (with `--reseller-scenarios`)
- Two-router tests (with `--two-router`)
- Virtual WiFi: worker-provisioned hwsim (opt-in via `--hwsim`, 2 radios, AP bringup, STA tests skip)
- vwifi cross-VM WiFi relay (opt-in via `--vwifi`, real STA scan/association between VMs)
- NDS portal tests (over wired br-lan, no cross-VM WiFi needed)
- Portal verify tests (gated by `_skip_unless_nds_responsive()`)
- Local mints: CDK V2, Nutshell V1, Nutshell V2 (all FakeWallet)

### Debian Container Client Cashu Token Flow

The cloud lab uses a Debian QEMU VM (`10.99.99.100`) as the test client. Visual portal tests record the full Cashu payment flow from the user's perspective using Playwright inside this container. Here's how tokens get from the test framework to the browser:

```
┌─ Test Machine / GCP Host ────────────────────────────────────┐
│                                                               │
│  1. pytest creates CashuMint(venv, mint_url)                 │
│  2. cashu.warmup() → pre-initialize wallet DB                │
│  3. cashu.mint(4) → subprocess: cashu send 4 --legacy       │
│     Returns: "cashuAeyJwcm..."                               │
│  4. adb.signal_token(token) → SSH to container:              │
│     echo 'cashuAeyJwcm...' > /tmp/tg-token                   │
│                                                               │
└───────────────────────────│───────────────────────────────────┘
                            │ SSH
                            ▼
┌─ Debian QEMU Container (10.99.99.100) ───────────────────────┐
│                                                               │
│  5. Playwright recording script (already running):            │
│     - Launched by adb.start_portal_recording() via SSH       │
│     - Chromium navigated to http://10.99.99.1:2050/ (portal) │
│     - Screenshots: 01-portal-unpaid.png                       │
│     - Writes /tmp/tg-portal-ready (signals: browser loaded)  │
│                                                               │
│  6. Token signal polling loop (inside Playwright script):    │
│     for _ in range(240):                                      │
│       if /tmp/tg-token exists: → read token, break           │
│       sleep(0.5)                                              │
│                                                               │
│  7. submit_token(token):                                      │
│     - Find token input (textarea / input[name*=token])       │
│     - token_input.fill(token)                                 │
│     - Screenshot: 02-token-filled.png                         │
│     - Click submit button (purchase/submit/pay)              │
│     - Poll for auth markers (data-sm="authed", "remaining")  │
│     - Screenshot: 03-portal-paid.png                          │
│                                                               │
│  8. Verify internet access:                                   │
│     - Navigate to http://1.1.1.1                              │
│     - Screenshot: 04-internet-access.png                      │
│                                                               │
│  9. Close browser context → save video to                     │
│     /tmp/tg-e2e/portal-flow.webm                              │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

**Key synchronization points:**

| Signal | Path | Purpose |
|--------|------|---------|
| `/tmp/tg-portal-ready` | Written by Playwright | Test waits for this before minting token (portal loaded) |
| `/tmp/tg-token` | Written by test via `adb.signal_token()` | Playwright reads token and fills it into portal |
| `/tmp/tg-paid` | Alternative to `tg-token` | For out-of-band payment; Playwright reloads portal to capture paid state |

**Mint URL config chain:**

1. `worker.py` → `select_test_mint()` probes backend for V2 support
2. Falls back to Nutshell V1 (`http://v1.testnut.nutshell.lan:8385`) for Go backend
3. Writes chosen URL to `.env` as `TOLLGATE_TEST_MINT_URL`
4. `conftest.py` `cashu()` fixture reads `TOLLGATE_TEST_MINT_URL` from env
5. `CashuMint` or `CdkCliWallet` uses this URL for all minting operations

**Recording lifecycle (`lib/clients/container.py`):**

- `start_portal_recording()` — SSH to container, launches Playwright script as background process
- `wait_for_portal_ready(timeout=90)` — polls for `/tmp/tg-portal-ready` on container
- `signal_token(token)` — writes `/tmp/tg-token` on container
- `finish_portal_recording(output_dir, timeout=120)` — waits for Playwright script to complete, collects screenshots + video via SCP

### What needs improvement

- **hwsim STA mode**: Currently reconfigures radio1 to STA. Could use a 3rd radio for dedicated STA while keeping both AP radios stable.
- **Cross-VM WiFi**: Not possible with current QEMU topology. Portal testing works via wired ethernet through nodogsplash on br-lan.
- **Mint health/degraded tests**: Most skip in cloud lab because feature detection gates are conservative — could be relaxed with local mint manipulation
- **Multi-VM topology**: Currently limited to 2 OpenWrt VMs. Future: per-environment bridge isolation for dozens of VMs

### Out of scope for cloud

Phone tests, physical-router LuCI Playwright, destructive sysupgrade — use `test-pr.sh` on lab hardware.

## AI Agent Rules

- **Bug reports filed by AI agents must go to the Amperstrand fork only** ([Amperstrand/tollgate-module-basic-go](https://github.com/Amperstrand/tollgate-module-basic-go)), NOT the upstream OpenTollGate repo. This avoids noise for upstream maintainers. Filing on OpenTollGate is acceptable only when a human explicitly requests it.
- AI agents should not review PR #86 or ask @c0brador to merge it.

## Security Notes

- Built firmware images contain credentials (SSH key + password) in the uci-defaults script. Treat images as sensitive.
- Credentials are saved to `credentials/` (gitignored, mode 600).
- SSH key comments are stripped before embedding (no user@host in images).
- WAN SSH is acceptable for lab routers. Disable on production.
- The ASU server sees build requests over HTTPS. Acceptable for test firmware.

## Hardware Mutex Protocol

The Makefile enforces a hardware lock (`hardware.lock`) to prevent concurrent access to physical routers and ESP32 boards by multiple sessions (e.g., multiple LLM agents or developers).

### How it works

- `make lock PHASE="description"` creates `hardware.lock` with session info (user, hostname, branch, timestamp, phase)
- All hardware test targets call `require_hardware_lock` and fail if the lock is missing
- `make unlock` removes the lock
- `make force-unlock` force-releases (use with caution)
- ESP32 boards have separate per-board locks (`esp32/locks/board-a.lock`, etc.)

### Router label convention

Routers are identified by label (e.g., `alpha`, `beta`) from `mint-health/routers.env` or `upstream-wifi/routers.env`, not raw IPs. This allows the same Makefile targets to work across different lab configurations:

```bash
# routers.env format
ROUTER_ALPHA_HOST=192.168.13.112
ROUTER_ALPHA_SERIAL=/dev/serial-alpha
ROUTER_BETA_HOST=192.168.13.113
ROUTER_BETA_SERIAL=/dev/serial-beta
```

### Lock file location

- Router lock: `./hardware.lock` (project root)
- ESP32 board locks: `esp32/locks/board-{a,b,c}.lock`
- All lock files are gitignored (`**/*.lock`)

## Mint Health iptables Simulation

Degraded mode (mint unreachable) is simulated via iptables rules that block traffic to the mint's resolved IP while preserving NetBird SSH connectivity:

```bash
# Block mint (force degraded mode)
iptables -A OUTPUT -d <mint-ip> -j DROP

# Unblock mint (restore full mode)
iptables -D OUTPUT -d <mint-ip> -j DROP
```

The Makefile targets (`block-mint`, `unblock-mint`) handle hostname resolution and iptables automatically.

## Two-Router Test Topology

Hardware tests use two routers connected via NetBird:

| Role | Label | Purpose |
|------|-------|---------|
| Alpha | `alpha` | Primary test target |
| Beta | `beta` | Secondary / upstream TollGate |

Tests like `smoke-upstream` and `smoke-pin-upstream` require both routers. Alpha acts as the downstream TollGate (payment gateway), and Beta provides the upstream network connection.

## Serial Console Operational Notes

### USB-TTL adapters

- Use CP2102 or CH340-based USB-TTL adapters (3.3V, NOT 5V)
- GL-MT3000 serial pins: TX, RX, GND (no flow control needed)
- Baud rate: 115200

### udev rules

Create `/etc/udev/rules.d/99-serial-routers.rules` with stable symlinks:

```
SUBSYSTEM=="tty", ATTRS{serial}=="CP2102_ABCD", SYMLINK+="serial-alpha"
SUBSYSTEM=="tty", ATTRS{serial}=="CP2102_EFGH", SYMLINK+="serial-beta"
```

### Serial target prefixes

| Prefix | Transport | Use Case |
|--------|-----------|----------|
| `r-` | SSH | Normal operations |
| `s-` | Serial | No-network scenarios: cold boot, recovery |
| `h-` | Hybrid | Tries SSH first, falls back to serial |

### Emergency serial recovery

If a router is stranded (broken config, no network), use serial recovery:

```bash
make serial-recovery ROUTER=alpha CMD="sed -i '/nofee.testnut/d' /etc/hosts && /etc/init.d/tollgate-wrt restart"
```

## ESP32 Board Flashing Notes

- **SPIFFS generation**: `make arch-generate-spiffs` auto-detects WPA security mode (WPA2-PSK, WPA3-SAE, or open) from the board's running config
- **Per-board firmware variants**: Boards A, B, and C can run different firmware (multi-mint, relay, tollgate_core)
- **Flash targets**: `esp32-flash-a/b/c` require the corresponding board lock
- **Monitor targets**: `esp32-monitor-a/b/c` provide serial console access to each board

## Arch (tollgate_core) Component Testing

The arch test suite validates the `tollgate_core` firmware on ESP32 (Board A):

1. `arch-build` - Cross-compile firmware
2. `arch-flash-a` - Flash to Board A
3. `arch-generate-spiffs` - Generate filesystem with auto-detected WPA config
4. `arch-flash-spiffs-a` - Flash filesystem
5. `arch-test-full` - Run all E2E tests (~4min): smoke, network, API, DNS/firewall, auth reset, session expiry

Tests are ordered by dependency and run sequentially. The full suite validates WiFi AP, captive portal, DNS resolution, payment flow, and session management.

## Writing New Tests — Quick Reference

1. **API test** (no phone): Create `tests/api/test_<feature>.py`. Use `@pytest.mark.api` and at least one tier marker (`smoke`, `critical`, or `extended`). Use the `router` fixture.

2. **Phone test** (needs ADB): Create `tests/phone/test_<feature>.py`. Use `@pytest.mark.phone`. Use `router`, `adb`, `cashu`, and `connected_wifi` fixtures.

3. **Gate with feature detection**: Add a `_skip_if_no_<feature>(router)` helper at the top of the test file. Call it at the start of each test function.

4. **Gate for bug regression**: Use `gate_bug_fix()` from `lib/helpers`. Pass a boolean indicating if the fix is present, plus `bug_id` and `fix_pr` for traceability.

5. **Backend-specific**: Use `@pytest.mark.go_only` or `@pytest.mark.rust_only` if the test only applies to one backend.

6. **Scenario test**: Create `tests/scenarios/test_<scenario>.py`. Use `@pytest.mark.hardware`. Register in `config/make-pytest-map.yaml`.

7. **Reference format for bug cross-links**:
   ```
   See: https://github.com/OpenTollGate/tollgate-knowledgebase/tree/main/incidents/YYYY-MM-DD_slug.md
   ```

## Cashu Token Version Compatibility

The Go backend (gonuts) only supports V1 and V3 Cashu tokens. V4 tokens are rejected with `"Invalid cashu token: invalid token: invalid V3 token"`.

| Token Version | Prefix | Encoding | Go Backend | Notes |
|---------------|--------|----------|------------|-------|
| V1 | `cashuA` | Base64 JSON | **Accepted** | Legacy format |
| V3 | `cashuAeyJ` | Base64 JSON | **Accepted** | Current standard, tested with 378-char testnut tokens |
| V4 | `cashuB` | Binary CBOR | **Rejected** | Go returns `kind:21023` error |

Users with modern Cashu wallets (eNuts, cashu.me with latest CDK) may produce V4 tokens that the Go backend cannot process. This is a backend limitation, not a mint-specific issue.

Full findings and test matrix: `docs/portal-test-findings.md`.

### Keyset ID compatibility (separate from token version)

| Keyset Version | Prefix | Go Backend | Notes |
|---------------|--------|------------|-------|
| V1 | `00` (16 hex chars) | **Required** | gonuts only supports V1 keysets |
| V2 | `01` (66 hex chars) | **Fatal crash** | Backend refuses to start |

Only `testnut.cashu.exchange` returns V1 keysets. CDK mints return V2 keysets and will crash the Go backend on startup.

## Captive Portal Flow (Physical Router)

### Framework approach (recommended)

The framework does NOT rely on Android's native "Sign in to network" popup. It bypasses it:

1. `reset_state()` — deauth client, restart NDS + backend
2. `wifi._connect_to_wifi()` — disconnect WiFi, reconnect to TollGate SSID
3. `_open_portal_on_phone()` — **manually opens portal URL in browser** via `am start -a android.intent.action.VIEW`

This is deterministic and works across Android versions/OEMs. The native popup is unreliable — Android caches network validation results and may not re-check for minutes after deauthentication.

### ADB token typing

- `adb shell input text` silently truncates at ~200 chars. Must use 80-char chunks with 0.3s delay for tokens ≥200 chars.
- The portal UI only shows ~30 chars of the token in the input field, but the full token IS present.
- The keyboard must be dismissed (tap outside input field) before the Purchase button is visible.
- The portal Paste button (CB002) is broken on Android 14 Firefox.

### firewall-tollgate masquerade bug

The TollGate `.ipk` creates `/etc/config/firewall-tollgate` as a UCI-format fw4 include. fw4 silently rejects it because the syntax is invalid for an include file. The masquerade/NAT rule never loads. Authenticated clients can reach the router but not the internet.

**Fix**: Rename the file to `.disabled`, remove the UCI include reference, restart fw4.

**Impact**: `pay_direct()` tests don't catch this because they SSH to the router and hit the backend directly. Only phone tests that verify actual internet access would detect the missing masquerade.
