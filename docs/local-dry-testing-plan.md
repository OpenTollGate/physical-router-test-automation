# Local Testing Architecture

## Overview

Three layers of local testing, each covering progressively more of the
TollGate stack. All results stay local (gitignored `results/` directory) —
`LocalProvider` has `can_publish = False`.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  This machine (host)                                            │
│  10.99.99.2 via tg-poc-br bridge                               │
│                                                                 │
│  Layer 1: Process-based (fastest, no NDS/WiFi)                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                      │
│  │ Mock Mint│←─│ Go Backend│←─│ Vite Dev │                      │
│  │ (:3338)  │  │ (:2121)   │  │ (:5173)  │                      │
│  │ Python   │  │ + ndsctl  │  │ React    │                      │
│  │ coincurve│  │   stub    │  │ Portal   │                      │
│  └──────────┘  └──────────┘  └──────────┘                      │
│       ↑                                                         │
│       │ 6/6 Playwright tests pass in 7.2s                      │
│       │ 7/7 API tests pass                                      │
│                                                                 │
│  Layer 2: OpenWrt VM (real NDS, real firewall)                 │
│  ┌─────────────────┐    ┌──────────────────┐                   │
│  │ OpenWrt VM      │    │ Debian Client VM │                   │
│  │ 10.99.99.1      │    │ 10.99.99.100     │                   │
│  │                 │    │                  │                   │
│  │ ✅ Real NDS     │◄──►│ (Playwright)     │                   │
│  │ ✅ Real ndsctl  │    │                  │                   │
│  │ ✅ Real iptables│    │                  │                   │
│  │ ✅ tollgate-wrt │    │                  │                   │
│  │ ✅ Portal /www/ │    │                  │                   │
│  └─────────────────┘    └──────────────────┘                   │
│          ▲                                                      │
│     CDK Mint (:8383) or Mock Mint (:3338)                     │
│     or testnut.cashu.exchange (public)                         │
│                                                                 │
│  Layer 3: Virtual WiFi (mac80211_hwsim — future)              │
│  ┌─────────────────┐    ┌──────────────────┐                   │
│  │ OpenWrt VM      │    │ Client VM        │                   │
│  │ wlan0 (AP)      │~~→│ wlan1 (station)  │                   │
│  │ hostapd         │ RF │ wpa_supplicant   │                   │
│  │ mac80211_hwsim  │    │ mac80211_hwsim  │                   │
│  └─────────────────┘    └──────────────────┘                   │
│                                                                 │
│  CDK Mint (:8383) — already running, V2 keysets               │
│  Mock Mint (:3338) — our Python mock, V1 keysets              │
│  testnut.cashu.exchange — public test mint, V1 keysets        │
└─────────────────────────────────────────────────────────────────┘
```

## What Each Layer Tests

| What | Layer 1 (Process) | Layer 2 (OpenWrt VM) | Layer 3 (Virtual WiFi) |
|---|---|---|---|
| Token payment (V3/V4) | ✅ Mock mint | ✅ Real CDK/testnut | ✅ Same |
| Rate limiting | ✅ | ✅ | ✅ |
| Error sanitization | ✅ | ✅ | ✅ |
| Degraded mode | ✅ | ✅ | ✅ |
| Portal UI | ✅ Vite dev | ✅ Production build | ✅ Same |
| Advertisement | ✅ (with ad interception) | ✅ Real backend | ✅ Same |
| **NDS firewall gating** | ❌ Stub | ✅ **Real ndsctl** | ✅ Same |
| **Captive portal redirect** | ❌ No interception | ✅ **Real NDS** | ✅ Same |
| **WiFi client behavior** | ❌ No radio | ❌ Wired only | ✅ **Virtual radio** |
| **Double-spend (on-chain)** | ❌ Mock | ✅ Real mint | ✅ Same |

## Layer 1: Process-Based (Current Default)

**Files:**
- `lib/mock_mint.py` — Python mock Cashu mint with real secp256k1 crypto
- `lib/local_process.py` — Process orchestrator (start/stop backend + mint)
- `tests/api/test_local_payment.py` — 7 API test scenarios
- `tests/captive-portal.local.spec.mjs` — 6 Playwright browser tests
- `scripts/local-test.sh` — One-command runner

**Key design decisions:**
- Mock mint persists keyset to `/tmp/mock-mint-keyset.json` (survives restarts)
- `keyboard.insertText()` used instead of `fill()` (React onChange workaround)
- `setupAdInterception()` patches `window.fetch` to inject pricing data
  (backend advertisement loses `price_per_step` when mints are unreachable)
- Port conflict detection kills stale processes before starting
- ndsctl stub returns `{"id":1}` (int, not string — matches Go struct)

**Results:** 6/6 browser tests (7.2s), 7/7 API tests

## Layer 2: OpenWrt VM (Real NDS)

**Already running on this machine:**
- OpenWrt VM at `10.99.99.1` (SSH as root, KVM-accelerated)
- Debian client VM at `10.99.99.100`
- CDK mint at `10.99.99.2:8383` (V2 keysets, `cdk-mintd/0.16.0`)
- `tg-poc-br` bridge connecting all three

**What this enables:**
- **NDS firewall gating**: `ndsctl auth <mac>` creates real iptables rules.
  Traffic is actually blocked/unblocked. Verify with `iptables -L` on the VM.
- **Captive portal redirect**: NDS intercepts HTTP requests from the client VM
  and redirects to `http://10.99.99.1:2121/`. After payment, redirect stops.
- **Real token verification**: Backend talks to real CDK mint, verifies real
  proofs, performs real swaps.

**Empirically proven (July 2026):**
- V4+V2 (CDK token) → `NUT02: ID length invalid` — proves issue #282
- Advertisement shows `unit=sat` after sat fix deployment
- NDS returns real client list via `ndsctl json`
- Whoami resolves MAC via ARP table

**Test runner:**
```bash
TOLLGATE_VM_PROVIDER=local \
TOLLGATE_SSH_HOST=10.99.99.1 \
    pytest tests/api -m virtual_lab
```

## Layer 3: Virtual WiFi (Future)

**mac80211_hwsim** creates software-only 802.11 radios in the kernel:

```bash
# On host (requires root):
modprobe mac80211_hwsim radios=2
# Creates phy0 (for AP) and phy1 (for client)

# In OpenWrt VM:
# - Pass hwsim radios via QEMU USB passthrough or virtio-wifi
# - Run hostapd on wlan0 (AP mode)
# - Create wireless interface in /etc/config/wireless

# In client VM:
# - wpa_supplicant on wlan1 (station mode)
# - Associate with OpenWrt AP
# - Traffic goes through NDS → real captive portal UX
```

**Current status:**
- `mac80211_hwsim.ko.zst` EXISTS on the host kernel (`/lib/modules/6.17.0-35-generic/`)
- Module is NOT loaded
- OpenWrt VM does NOT have hwsim loaded
- `iw list` shows `phy0` on host (real wireless card, not hwsim)
- `vwifi` and `BlossomFS` binaries documented in prta AGENTS.md

**What it would enable:**
- Full WiFi association/authentication/encryption
- Real captive portal detection (OS-level popup)
- Bandwidth metering through WiFi interface
- Client roaming between virtual APs (reseller mode)

**Limitations:**
- Requires kernel module pass-through to OpenWrt VM (complex QEMU config)
- Or: run hwsim on host and bridge to VMs (different architecture)
- OpenWrt kernel must have mac80211_hwsim compiled in

## Privacy Guarantees

All local testing providers have `can_publish = False`:

```python
# lib/cloud_lab/provider.py
class LocalProvider(VMProvider):
    can_publish = False  # ← Results NEVER leave the machine

class PhysicalProvider(VMProvider):
    can_publish = False  # ← Same for physical routers
```

This means:
- Test results stored in gitignored `results/` directory only
- No Nostr event publishing
- No Blossom/uploads
- No screenshots or logs sent to external services
- Only `SHCProvider` and `GCPProvider` (ephemeral cloud VMs) have `can_publish = True`

The `test-pr.sh` script checks `can_publish` before calling `publish-report.sh`
(line ~323). This is a hard gate — not configurable per-test.

## Provider Abstraction

All three layers use the same provider abstraction:

```
TOLLGATE_VM_PROVIDER=local      → LocalProvider (QEMU on this machine)
TOLLGATE_VM_PROVIDER=shc        → SHCProvider (SHC cloud API)
TOLLGATE_VM_PROVIDER=gcloud     → GCPProvider (GCP nested-KVM)
TOLLGATE_VM_PROVIDER=pulumi     → PulumiSHCProvider (Pulumi Automation API)
TOLLGATE_VM_PROVIDER=physical   → PhysicalProvider (real router via SSH)
```

The test code (`tests/api/*.py`, `tests/captive-portal*.spec.mjs`) is
provider-agnostic. The provider only affects how the test target (router
IP, SSH access) is provisioned. The tests themselves are identical
regardless of provider.

## Sat vs Sats Standardization

All components now use `"sat"` (NUT-00 standard, singular):
- Backend config defaults: `"sat"` (was `"sats"` — fixed)
- Mock mint tokens: `"sat"`
- Test config templates: `"sat"`
- gonuts-tollgate `Unit.String()`: `"sat"`
- @cashu/cashu-ts tokens: `"sat"`

The mismatch caused CU109 (unit mismatch) in the portal when validating
real Cashu tokens against backend-configured access options.

## Known Issues

See `docs/known-issues.md` for:
- IPv4 loopback stale state (fix: `ip link set lo down/up`)
- Backend advertisement losing `price_per_step` (fixed: `GetAllConfiguredMintConfigs`)
- Playwright `fill()` not triggering React onChange (workaround: `keyboard.insertText()`)
