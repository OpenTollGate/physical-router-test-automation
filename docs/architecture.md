# TollGate Test Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              OPERATOR MACHINE                                │
│                                                                             │
│  ┌─────────────┐    ┌──────────────────┐    ┌───────────────────────────┐  │
│  │   conwrt    │    │ cloud-lab.py     │    │    tests.tollgate.me      │  │
│  │   wizard    │    │ submit --cloud   │    │    (dashboard SPA)        │  │
│  │             │    │   pulumi         │    │                           │  │
│  │ amperstrand │    │                  │    │  Reads Nostr kind 30078   │  │
│  │ .github.io  │    │ OPENWRT_VERSION  │    │  Fetches Blossom blobs    │  │
│  │  /conwrt/   │    │ --portal         │    │  Renders test hierarchy   │  │
│  │  wizard     │    │ --publish        │    │  + inline screenshots     │  │
│  └──────┬──────┘    └────────┬─────────┘    └───────────────────────────┘  │
│         │                    │                       ↑                     │
│         │            ┌───────┴────────┐              │                     │
│         │            │  Pulumi + SHC  │              │ Nostr events        │
│         │            │  (shc-pulumi)  │              │ + Blossom URLs      │
│         │            └───────┬────────┘              │                     │
└─────────┼────────────────────┼───────────────────────┼─────────────────────┘
          │                    │                       │
          │           ┌────────┴────────┐     ┌────────┴────────────┐
          │           │  SHC Cloud VM    │     │   Nostr Relays      │
          │           │  (Dev 2C/8GB)    │     │  damus.io           │
          │           │                  │     │  nos.lol            │
          │           │ Bootstrap:       │     │  relay.cashu.email  │
          │           │  apt packages    │     └─────────────────────┘
          │           │  nak CLI         │              ↑
          │           │  nsec (Nostr key)│              │
          │           │  Python venv     │     ┌────────┴────────────┐
          │           │  Cashu venv      │     │   Blossom Servers   │
          │           │  CDK + Nutshell  │     │  blossom.psbt.me    │
          │           │  QEMU images     │     │  blossom.primal.net │
          │           │  gh CLI          │     │  (R2 + Cloudflare   │
          │           │  KVM modules     │     │   Worker)            │
          │           │                  │     └─────────────────────┘
          │           │ Worker pipeline: │              ↑
          │           │  1. Boot QEMU VMs│              │ Upload artifacts
          │           │  2. Deploy toll- │              │ (screenshots, logs,
          │           │     gate-wrt     │     ┌────────┘ junit, videos)
          │           │  3. Start mints  │
          │           │  4. Run tests    │
          │           │  5. Collect      │
          │           │  6. Publish      │
          │           └──────────────────┘
          │                    │
          │           ┌────────┴────────────────────────────────┐
          │           │              QEMU VMs                    │
          │           │                                          │
          │           │  ┌──────────────┐  ┌──────────────────┐ │
          │           │  │ OpenWrt VM   │  │  Debian Client   │ │
          │           │  │ 10.99.99.1   │  │  10.99.99.100    │ │
          │           │  │              │  │                  │ │
          │           │  │ tollgate-wrt │←→│  Playwright      │ │
          │           │  │ :2121 (API)  │  │  (LuCI + portal  │ │
          │           │  │              │  │   visual tests)  │ │
          │           │  │ nodogsplash  │  │                  │ │
          │           │  │ :2050 (NDS)  │  │  cashu CLI       │ │
          │           │  │              │  │  (payment tokens)│ │
          │           │  │ uhttpd :80   │  │                  │ │
          │           │  │ (captive     │  │  pytest          │ │
          │           │  │  portal)     │  │  (API tests via  │ │
          │           │  │              │  │   SSH to OWrt)   │ │
          │           │  └──────────────┘  └──────────────────┘ │
          │           │                                          │
          │           │  ┌──────────────────────────────────┐   │
          │           │  │ Host VM services                 │   │
          │           │  │ 10.99.99.2                      │   │
          │           │  │                                  │   │
          │           │  │ CDK V2 mint    :8383  ────────── │   │
          │           │  │ Nutshell V2    :8384  Cashu mints │   │
          │           │  │ Nutshell V1    :8385  (FakeWallet)│   │
          │           │  │                                  │   │
          │           │  │ tg-poc-br bridge (10.99.99.0/24) │   │
          │           │  │ mgmt-br bridge  (10.99.97.0/24)  │   │
          │           │  └──────────────────────────────────┘   │
          │           └────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        PHYSICAL ROUTER (x1860)                          │
│                                                                         │
│  conwrt wizard generates shell script:                                  │
│                                                                         │
│  1. Flash stock OpenWrt (U-Boot recovery)                               │
│  2. WiFi STA → join upstream WiFi                                       │
│  3. opkg/apk install tollgate-wrt (Cashu + Lightning gateway)          │
│  4. opkg/apk install umdns (.local mDNS resolution)                     │
│  5. opkg/apk install configurationwizzard (net4sats portal UI)          │
│  6. uci set system.hostname='net4sats' + dnsmasq restart                │
│  7. uci set nodogsplash gatewayname='net4sats'                          │
│      uci set nodogsplash gatewaydomainname='net4sats.lan'               │
│  8. uci set network lan → model-specific subnet                         │
│                                                                         │
│  Result: net4sats.lan + net4sats.local → router IP                      │
│          Captive portal at http://net4sats.lan/                         │
│          Payment via Cashu tokens or Lightning                          │
└─────────────────────────────────────────────────────────────────────────┘
```

## Package Format Matrix

```
                    OpenWRT 24.10.x          OpenWRT 25.12.x
                    ─────────────────         ─────────────────
Package manager     opkg (.ipk)               apk (.apk)
Image format        ext4-combined.img.gz      ext4-combined.img.gz
Firmware URL        downloads.openwrt.org/    downloads.openwrt.org/
                    releases/24.10.7/          releases/25.12.5/
                    targets/x86/64/            targets/x86/64/

tollgate-wrt        .ipk from Blossom         .apk from Blossom
                    (opkg install)             (apk add --allow-untrusted)

configurationwizzard .ipk from Blossom        .apk from Blossom  
                     (opkg install)            (apk add --allow-untrusted)

umdns               .ipk from OpenWrt repo    .apk from OpenWrt repo
                    (opkg install umdns)       (apk add umdns)
```

## Publishing Pipeline

```
Test completes
      │
      ▼
collect_and_render
  ├── parse JUnit XML → summary.json (test hierarchy)
  ├── generate report/index.html
  └── capture screenshots + videos
      │
      ▼
result_publisher (nostr_publish package)
  ├── scan_directory (secret detection + redaction)
  ├── upload each clean file to Blossom (BUD-02 PUT)
  │   └── blossom.primal.net (primary)
  │   └── blossom.psbt.me (secondary, whitelisted)
  ├── publish kind 30078 summary event to Nostr relays
  │   ├── wss://relay.damus.io
  │   ├── wss://nos.lol
  │   └── wss://relay.cashu.email
  └── write manifest.json
      │
      ▼
tests.tollgate.me (dashboard SPA)
  ├── subscribes to kind 30078 from all runners
  ├── filters by tag "tollgate"
  ├── on run click: fetch summary.json from Blossom
  ├── builds hierarchical test tree (suite → test → artifacts)
  ├── lazy-loads screenshots from Blossom (IntersectionObserver)
  ├── inline <video controls> for .webm files
  └── auto-expands failed tests for immediate screenshot visibility
```

## Test Matrix (4 combinations)

```
                    builtin portal              net4sats portal
                    ───────────────              ───────────────
OpenWRT 24.10.7     tollgate-wrt.ipk            + configurationwizzard.ipk
(opkg)              Default captive portal      Branded net4sats portal UI
                    --portal builtin             --portal net4sats

OpenWRT 25.12.5     tollgate-wrt.apk            + configurationwizzard.apk
(apk)               Default captive portal      Branded net4sats portal UI
                    --portal builtin             --portal net4sats
```

## Improvement Opportunities

### Reliability
1. **Auto-publishing**: The `report.py` subprocess inherits `BLOSSOM_SERVER` from the worker env, but the worker's `.env` file sets it to `blossom.psbt.me`. Fix: pass `--blossom-server` explicitly in the subprocess command (already done in shc_submit.py, but worker `.env` overrides it)
2. **VM lifecycle**: Pulumi's `auto_cancel` removed, but Pulumi stack cleanup on process exit still kills VMs. Fix: use `--wait` or detach stack from process lifecycle
3. **BlossomFS cache**: The pre-built binary cache on Blossom uses a different nsec than the CI's NSEC_HEX. Fix: re-cache with the correct key

### Coverage
4. **conwrt deploy mode**: `TOLLGATE_DEPLOY_MODE=conwrt` is wired but untested in cloud lab. Would validate the wizard's exact commands
5. **Two-router reseller**: `--two-router --reseller-scenarios` tests upstream autopay (needs second QEMU VM)
6. **Virtual WiFi**: `--vwifi` tests AP/STA mode with mac80211_hwsim (needs BlossomFS/vwifi compilation)

### Dashboard
7. **Version comparison view**: Side-by-side OWRT 24 vs 25 results
8. **Trend charts**: Pass rate over time per OpenWRT version
9. **Faster screenshot loading**: Pre-generate thumbnails on the VM before upload
10. **Portal comparison**: Screenshot diff between builtin and net4sats portals
