# TollGate Testing Architecture

## Overview

Three testing venues, sharing one test suite (`tests/phone/`, `tests/browser/`, `tests/api/`), one router abstraction (`lib/router.py`), and one film recording plugin (`lib/film_recorder.py`).

```
┌─────────────────────────────────────────────────────────────────┐
│  Test Suite (pytest)                                            │
│  tests/phone/     — payment, metering, expiry, session, etc.    │
│  tests/browser/   — portal UI, captive portal, dashboard        │
│  tests/api/       — backend API, protocol compliance            │
│                                                                  │
│  --film          — record phone screen, compose evidence film   │
│  --client X      — phone client: adb | cuttlefish | container   │
│  --backend Y     — router backend: go | rust-basic | rust       │
└──────────────────────────┬──────────────────────────────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
   Physical Router    QEMU OpenWrt      Cuttlefish Lab
   (GL-MT3000)        (24.10 VM)        (Android VM + hwsim WiFi)
   wired or WiFi      Debian client     real phone UX
   prod-adjacent      fast iteration    captive portal detection
```

## Phone Client Modes

| Mode | Flag | Description | Best for |
|------|------|-------------|----------|
| `adb` | `--client adb` | Physical phone via USB adb | Real hardware validation |
| `cuttlefish` | `--client cuttlefish` | Cuttlefish Android VM on ai-legion | Phone UX testing without hardware |
| `container` | `--client container` | Debian VM via SSH | Fast iteration, no phone needed |
| `mac` | `--client mac` | Mac's WiFi as client | Quick desktop checks |
| `linux` | `--client linux` | Linux desktop WiFi | CI runners |

### Cuttlefish Mode

The Cuttlefish Android VM runs on ai-legion with mac80211_hwsim virtual WiFi.
The phone connects to a TollGate access point (also a VM) over simulated 802.11.

```bash
# Environment
export TOLLGATE_CF_HOST=ai-legion
export TOLLGATE_CF_SERIAL=0.0.0.0:6520
export TOLLGATE_CLIENT=cuttlefish

# Run phone tests
pytest tests/phone/ --client cuttlefish --backend go
```

The `CuttlefishClient` (`lib/clients/cuttlefish.py`) wraps all adb commands in
SSH to the host, providing the same interface as `ADBDevice`.

## Router Backends

| Backend | Flag | Description |
|---------|------|-------------|
| Go | `--backend go` | tollgate-module-basic-go (canonical) |
| Rust 1:1 | `--backend rust-basic` | tollgate-module-basic-rust |
| Rust experimental | `--backend rust` | tollgate-rs |

## Film Recording

```bash
# Record phone screen during tests, compose evidence film
pytest tests/phone/ --client cuttlefish --film

# With narration (requires edge-tts)
pytest tests/phone/ --client cuttlefish --film --film-narrate

# Custom output directory
pytest tests/phone/ --film --film-dir my-evidence/
```

The film plugin (`lib/film_recorder.py`) hooks into pytest:
- Starts `screenrecord` before each phone/browser test
- Pulls the clip after the test completes
- Composes all clips into a single film at session end
- Records test names, outcomes, and timestamps as chapter metadata

## Lab Setup

### Cuttlefish Lab (ai-legion)

Prerequisites on ai-legion:
- Cuttlefish virtual device (`cvd`) running
- mac80211_hwsim loaded
- OpenWrt AP VM (the Cuttlefish-managed AP)

```bash
# On the Mac: connect to the Cuttlefish phone
bash scripts/connect-cuttlefish.sh ai-legion

# Set environment
export TOLLGATE_CF_HOST=ai-legion
export TOLLGATE_SSH_HOST=192.168.94.2   # AP VM IP
export TOLLGATE_SSH_JUMP_HOST=ai-legion  # SSH through ai-legion
export TOLLGATE_BACKEND_URL=http://192.168.94.2:2121

# Run tests
pytest tests/phone/ --client cuttlefish --backend go --film
```

### QEMU Virtual Lab (local or ai-legion)

```bash
# Start OpenWrt 24.10 + Debian client VMs
python3 scripts/virtual-lab.py start-poc --host localhost

# Run tests (uses Debian VM as the phone)
pytest tests/phone/ --client container --backend go

# Clean up
python3 scripts/virtual-lab.py stop-poc --host localhost
```

### Physical Router (GL-MT3000)

```bash
# Point at the router
export TOLLGATE_SSH_HOST=192.168.1.1
export TOLLGATE_CLIENT_IP=<phone-ip>
export TOLLGATE_CLIENT_MAC=<phone-mac>

# Run tests with a physical phone
pytest tests/phone/ --client adb --backend go
```

## Known Issues

### OpenWrt 22.03 + NDS 5.0.2 (Cuttlefish AP VM) — issue #398

The iptables-nft translation on OpenWrt 22.03 strips port matchers from NDS
firewall rules. Pre-auth clients can't reach :2121 or :2051.

**Workaround**: flush all NDS chains and write explicit nftables rules:
```bash
# On the AP VM (via SSH)
for T in ip filter ip nat ip mangle; do
  for C in $(nft list table $T | grep "chain nds" | sed "s/.*chain \([^ ]*\).*/\1/"); do
    nft flush chain $T $C
  done
done
echo f > /proc/net/nf_conntrack
nft add table ip n4s_preauth
nft add chain ip n4s_preauth input '{ type filter hook input priority -2; policy accept; }'
nft add rule ip n4s_preauth input iifname "br-wifi0" counter accept
```

**Fix**: upgrade the Cuttlefish AP VM to OpenWrt 24.10 or later.

### Phone WiFi Association Flakiness

After multiple AP-side wifi reloads or daemon restarts, the wmediumd medium
can wedge. Recovery: full cvd restart on ai-legion.

## Issue Tracker

- [#398](https://github.com/OpenTollGate/tollgate-module-basic-go/issues/398) —
  NDS 5.0.2 on OpenWrt 22.03: iptables-nft strips port matchers
- [#399](https://github.com/OpenTollGate/tollgate-module-basic-go/issues/399) —
  Feature: deauth client on session expiry
- [#18](https://github.com/OpenTollGate/tollgate/issues/18) —
  WIFI-01: vendor IE (OUI 212121) is the real authenticity signal

## OpenWrt Version Compatibility

| Version | Status | Notes |
|---------|--------|-------|
| 25.12.5 | ✅ Recommended | apk package manager, kernel 6.12, ucode WiFi scripts |
| 24.10.x | ✅ Supported | opkg, fw4/nftables, net4sats-feed targets this |
| 22.03.x | ⚠️ Known issues | NDS iptables-nft bug (issue #398), use workaround |

### OpenWrt 25.12 Assessment (tested against 25.12.5)

**Safe for tollgate — no breaking changes expected:**

- **opkg → apk**: the net4sats-feed already provides apk packages for 25.12+
- **Kernel 6.12.71**: newer mac80211/cfg80211 (from 6.18) — mac80211_hwsim is
  stable across kernel versions; no API changes affecting hostapd or NDS
- **fw4/nftables**: unchanged from 24.10; NDS's nftables integration works
  correctly (the iptables-nft translation bug from 22.03 doesn't apply)
- **ucode WiFi scripts**: internal OpenWrt change; the wireless UCI interface
  (`uci set wireless.*`) and hostapd configuration are unchanged
- **dropbear 2025.89**: standard SSH, no auth changes

**One watch item**: WiFi scripts moved from shell to ucode. The `wifi reload`
and `wifi up/down` commands should work the same, but automated scripts that
parse WiFi script output may need adjustment.

### Recommendation for the Cuttlefish AP VM

Upgrade the AP VM from OpenWrt 22.03 to 25.12:

1. Download `openwrt-25.12.5-x86-64-generic-ext4-combined.img.gz`
2. Extract kernel (from boot partition) and rootfs
3. Replace `~/cf/etc/openwrt/images/openwrt_kernel_x86_64` and
   `openwrt_rootfs_x86_64` on ai-legion
4. Restart cvd

This eliminates the NDS 22.03 workaround and aligns with the net4sats-feed's
recommended version.
