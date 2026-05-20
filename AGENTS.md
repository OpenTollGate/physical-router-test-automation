# AGENTS.md — Operational Knowledge for physical-router-test-automation

This file contains hard-won operational knowledge for agents and humans working with physical OpenWrt routers (D-Link COVR-X1860, GL.iNet GL-MT3000). Read this before touching a router.

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

### Offline router deployment (no internet, no opkg update)

When a router has no internet (e.g., downstream/reseller behind a jump host), `opkg update` and `opkg install` will fail. You must manually SCP all packages and their dependencies.

**TollGate's declared dependencies** (from the Makefile `DEPENDS`):
- `nodogsplash`, `luci`, `jq`, `px5g-mbedtls`

**Test framework dependencies** (from `lib/deploy.py` `TEST_DEPS`):
- `curl`, `socat`, `nodogsplash`, `jq`, `luci`, `px5g-mbedtls`

Combined, these require ~52 packages (including transitive deps like `iptables-nft`, `kmod-*`, `rpcd`, `uhttpd`, `liblucihttp0`, etc.).

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

`scripts/cloud-lab.py submit` runs TollGate API tests in nested KVM on a GCP VM (`n2-standard-2` + the `SNAPSHOT_NAME` configured in `lib/cloud_lab/constants.py`). `tollgate-runner-baked-v2` is the safe baseline; newer baked snapshots must be verified before becoming the default.

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
- **Persistent caches** live under `/opt` (`/opt/tollgate-venv`, `/opt/cashu-venv`). Avoid `/tmp` for baked caches because it may be empty after boot.
- Re-bake snapshot when Debian packages, Playwright versions, gh/gcloud CLI, Python deps, cashu, or OpenWrt provisioning logic change.

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
- Pre-provisioned `openwrt-base.qcow2` (SSH enabled, password set, firewall rule added, network configured to 10.99.99.1)

The baker must run remote setup with `HOME=/root`, because the GCP startup worker also exports `HOME=/root`. If bake commands accidentally write to `/home/<ssh-user>/tollgate-virtual-lab`, the worker will read stale images from `/root/tollgate-virtual-lab`.

After baking, verify the snapshot with a throwaway cloud run or `cloud-lab.py up` before updating `SNAPSHOT_NAME` in `lib/cloud_lab/constants.py` to the new snapshot name (auto-incremented, e.g. `tollgate-runner-baked-v7`).

The worker (`lib/cloud_lab/worker.py`) detects pre-provisioned OpenWrt bases automatically — if SSH works within 15s of boot, serial provisioning is skipped. Falls back to serial provisioning for old snapshots without pre-provisioned bases.

### Commands

```bash
./scripts/cloud-lab.py submit --pr 42 --publish
./scripts/cloud-lab.py status-run --run-id <id>
./scripts/cloud-lab.py cleanup-stale   # delete RUNNING tollgate VMs >2h old
./scripts/cloud-lab.py cleanup-all      # delete ALL tollgate VMs
./scripts/bake-snapshot.py bake         # create new snapshot with deps pre-installed
```

### Timing (post-bake optimizations)

| Phase | Duration | Notes |
|---|---|---|
| VM boot + startup | ~2m | GCP startup script overhead |
| gh + venv + cashu | 0s (baked) | Pre-installed in snapshot |
| Boot OpenWrt + Debian VMs | ~30s | OpenWrt SSH-first detection, no serial |
| Deploy TollGate | ~50s | Download + install .ipk |
| Run tests | ~7m | Visual=101s, API=~5m |
| Collect + publish | ~30s | |
| **Total** | **~10-11min** | Down from ~15min pre-optimization |

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
