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

`scripts/cloud-lab.py submit` runs TollGate tests in nested KVM on a GCP VM (`n2-standard-2` + the `SNAPSHOT_NAME` configured in `lib/cloud_lab/constants.py`). The current snapshot is `tollgate-runner-baked-v8`; newer baked snapshots must be verified before becoming the default.

### Architecture

```
┌─ GCP Host VM (n2-standard-2, nested KVM) ──────────────────────┐
│                                                                  │
│  tg-poc-br (10.99.99.0/24) — management LAN                    │
│    ├── host: 10.99.99.2 (mints, NAT, orchestration)            │
│    ├── alpha: 10.99.99.1 (OpenWrt QEMU, TollGate under test)   │
│    ├── beta: 10.99.99.11 (optional second OpenWrt for 2-router) │
│    └── debian: 10.99.99.100 (Debian QEMU, Playwright, cashu)   │
│                                                                  │
│  tg-upstream-br (10.99.98.0/24) — simulated WAN (2-router)     │
│    ├── alpha WAN: DHCP from beta                                │
│    └── beta WAN: 10.99.98.1 (static, DHCP server)              │
│                                                                  │
│  Local mints (on host):                                         │
│    ├── CDK V2:      :8383 (V2 keysets, 01-prefix)              │
│    ├── Nutshell V2:  :8384 (V2 keysets)                         │
│    └── Nutshell V1:  :8385 (V1 keysets, 00-prefix, for Go)     │
│                                                                  │
│  OpenWrt VM (alpha):                                            │
│    ├── hwsim radios: kmod-mac80211-hwsim (pre-installed in v8)  │
│    ├── wpad-basic, iw-full, iwinfo (pre-installed in v8)        │
│    └── /etc/hosts maps mint DNS → 10.99.99.2                   │
│                                                                  │
│  Test flow (worker.py):                                         │
│    [1] Boot GCP VM from snapshot                                │
│    [2] Clone test repo, install deps (baked)                    │
│    [3] Boot OpenWrt + Debian QEMU VMs                           │
│    [4] Start 3 local mints (CDK + Nutshell V1 + V2)            │
│    [5] Deploy TollGate .ipk to OpenWrt                          │
│    [6] Select mint (CDK V2 if backend supports it, else V1)     │
│    [7] Run tests: visual → API → vl-scenarios → scenarios       │
│    [8] Collect results, publish to gh-pages, self-delete        │
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
| **two-router** | Two-router cloud (only with `--two-router`) | ~5min |

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

After baking, verify the snapshot with a throwaway cloud run or `cloud-lab.py up` before updating `SNAPSHOT_NAME` in `lib/cloud_lab/constants.py` to the new snapshot name (auto-incremented, e.g. `tollgate-runner-baked-v8`).

The worker (`lib/cloud_lab/worker.py`) detects pre-provisioned OpenWrt bases automatically — if SSH works within 15s of boot, serial provisioning is skipped. Falls back to serial provisioning for old snapshots without pre-provisioned bases.

### Commands

```bash
./scripts/cloud-lab.py submit --pr 42 --publish
./scripts/cloud-lab.py status-run --run-id <id>
./scripts/cloud-lab.py cleanup-stale   # delete RUNNING tollgate VMs >2h old
./scripts/cloud-lab.py cleanup-all      # delete ALL tollgate VMs
./scripts/bake-snapshot.py bake         # create new snapshot with deps pre-installed
```

### Timing (v8 snapshot with local mints)

| Phase | Duration | Notes |
|---|---|---|
| VM boot + startup | ~2m | GCP startup script overhead |
| gh + venv + cashu + cdk | 0s (baked) | Pre-installed in snapshot |
| Boot OpenWrt + Debian VMs | ~30s | OpenWrt SSH-first detection, no serial |
| Start local mints | ~5s | CDK + Nutshell V1 + V2, health checked |
| Deploy TollGate | ~50s | Download + install .ipk |
| Select test mint | ~5s | V2 probe, then V1 fallback |
| Run tests | ~20m | 94 tests with local mint (no timeouts) |
| Collect + publish | ~30s | |
| **Total** | **~25min** | Was ~10min before local mints (fewer tests ran) |

### mac80211_hwsim virtual WiFi (v8+)

The v8 snapshot includes WiFi simulation packages. Tests in `tests/api/test_mac80211_hwsim.py` verify:

| Test | What it verifies | Status |
|------|-----------------|--------|
| `test_install_hwsim_module` | Package pre-installed (baked) | SKIPPED (already loaded) |
| `test_load_hwsim_radios` | `modprobe mac80211_hwsim radios=2` | **PASSED** |
| `test_iw_list_shows_virtual_radios` | `iw list` shows ≥2 Wiphy | **PASSED** |
| `test_wlan_interface_ap_pears_after_ap_config` | UCI AP config → hostapd brings up interface | In progress (naming fix applied) |
| `test_iw_scan_executes` | `iw <iface> scan` runs without error | Depends on AP bringup |

OpenWrt names hwsim interfaces `phy<N>-ap0` (not `wlan0`). Tests must check `iw dev` output for interface names, not hardcoded `wlan0`.

hwsim supports STA mode — each radio can be AP, STA, or both simultaneously (2048 concurrent interfaces). Future work: configure radio1 as STA to enable upstream WiFi scan/connect tests in the cloud lab.

### What works in the cloud lab

- API tests (89 passed, 0 failed with local mint)
- Container client e2e portal payment (visual recording)
- Scenario tests: captive portal browser, mint health, boot hygiene, upstream WiFi CLI
- Reseller mode scenarios (with `--reseller-scenarios`)
- Two-router tests (with `--two-router`)
- Virtual WiFi: module load, radio detection, AP bringup
- Local mints: CDK V2, Nutshell V1, Nutshell V2 (all FakeWallet)

### What needs improvement

- **hwsim AP bringup test**: Interface naming fix applied but not yet verified in cloud run (interface is `phy2-ap0` not `wlan0`)
- **hwsim STA mode**: Second radio configured as station to enable `tollgate upstream scan` tests
- **Portal browser tests**: Nodogsplash doesn't serve `splash.html` without a preauthenticated client — tests skip correctly but could be improved
- **Mint health/degraded tests**: Most skip in cloud lab because feature detection gates are conservative — could be relaxed with local mint manipulation
- **Multi-VM topology**: Currently limited to 2 OpenWrt VMs. Future: per-environment bridge isolation for dozens of VMs

### Out of scope for cloud

Phone tests, physical-router LuCI Playwright, destructive sysupgrade — use `test-pr.sh` on lab hardware.

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
