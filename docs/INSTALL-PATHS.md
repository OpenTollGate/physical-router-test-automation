# TollGate — all install paths, and how to test each one

Target: **GL-MT3000** (`mediatek/filogic`, aarch64_cortex-a53), **OpenWrt 25.12.5**, on `192.168.1.1`.
Release under test: **`v0.6.0-alpha4-pre17`** (feed: `FreedomTechFeed/packages`).
Router root password: pass it yourself — never put it in a command line other people can read.

---

## Path 0 — reset to VANILLA OpenWrt (start every other path from here)

One line, from any machine with `curl`, `ssh` and `sha256sum`/`shasum`:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/felixfelix-bot/physical-router-test-automation/main/scripts/flash/wipe-to-vanilla-openwrt.sh) --ip 192.168.1.1
```

That run is **non-destructive**: it downloads the official image, checks its sha256 against
`downloads.openwrt.org/releases/25.12.5/targets/mediatek/filogic/sha256sums` (expected
`1ffa6526…8eaab8c6`), stages it on the router, verifies the staged bytes, and runs `sysupgrade -T`
(image test only).

To actually wipe and install:

```bash
bash <(curl -fsSL .../wipe-to-vanilla-openwrt.sh) --ip 192.168.1.1 --commit
```

- `sysupgrade -n` wipes `/etc/config` **and** `/etc/tollgate` — including any ecash in it.
- The script **refuses** while `/etc/tollgate` exists unless you pass `--allow-nonempty-wallet`.
  Drain first: `ssh root@192.168.1.1 '/usr/bin/tollgate wallet drain cashu --yes'`
- It prompts for `FLASH` before touching anything, waits for SSH to return (up to 4 min), then
  verifies: board, release, `tollgate: absent`, `nodogsplash: absent`.
- After it finishes the router is **fresh vanilla**: root has no password, no uplink is configured,
  TollGate is not installed. There is no internet until you set up WAN/STA — and with no uplink
  **no feed-based install path works**, which is the WAN-less case (Path 3).

---

## Path 1 — the installer (what you are running now)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/OpenTollGate/tollgate-installer/main/install-and-test.sh)
# → open http://localhost:8099/ and drive the UI
```

Headless variant (no browser), which is the form the review club can reproduce:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/OpenTollGate/tollgate-installer/main/install-and-test.sh) \
     <ROUTER_IP> '<ROUTER_PASSWORD>' '<LIGHTNING_ADDRESS>'
```

What it does: detects your platform, downloads `tollgate-installer`, serves the UI on `:8099`,
resolves the newest **feed alpha** (`v0.6.0-alpha4-pre17` today — module `2796d96c`, package
`0.6.0_alpha4_pre17`), deploys over SSH and verifies hostname, ports, DNS, LNURL, captive portal and
the health ad.

Notes:
- Because the installer resolves the newest feed alpha, the **installer path always lags the branch
  artifact** — never use it to test a fix that is not yet in the feed.
- It sets the operator choices the package path does not set for you: hostname, guest SSID, private
  credentials, DNS, LN address, root password. **Policy is the same on both paths; the choices are not.**

---

## Path 2 — package only, connected router (no installer)

On a vanilla router **with an uplink** (feeds reachable):

```bash
# 1. fetch the artifact for this arch from the release and verify it against the signed manifest
cd /tmp
curl -fLO https://github.com/FreedomTechFeed/packages/releases/download/v0.6.0-alpha4-pre17/tollgate-wrt_0.6.0_alpha4_pre17_aarch64_cortex-a53.apk
curl -fLO https://github.com/FreedomTechFeed/packages/releases/download/v0.6.0-alpha4-pre17/SHA256SUMS
sha256sum -c --ignore-missing SHA256SUMS        # expect: OK
# expected sha256 af7fc44f9c2312386b0144fc02c2c16282327304e4dfc0a86e28fd204b38c9a1

# 2. push it and install (deps nodogsplash + jq resolve from the router's own feeds — an uplink is required)
scp -O tollgate-wrt_0.6.0_alpha4_pre17_aarch64_cortex-a53.apk root@192.168.1.1:/tmp/
ssh root@192.168.1.1 'apk add --allow-untrusted /tmp/tollgate-wrt_0.6.0_alpha4_pre17_aarch64_cortex-a53.apk'

# 3. ⚠️ KNOWN ISSUE IN pre17 — the firewall is not reloaded by the package, so the newly shipped
#    nft guard is NOT active until a reload/reboot. Until pre18 ships the fix, do either:
ssh root@192.168.1.1 '/etc/init.d/firewall reload && /etc/init.d/nodogsplash restart'
#    (or just reboot the router — on a fresh install a reboot is the natural next step anyway)
```

Only the `.apk` installs on 25.x. The `.ipk` is for OpenWrt 24.10 and earlier (`apk` rejects it:
`v2 package format error`) — and it is a *different build*, not the same bytes.

Dependencies: the package declares `nodogsplash` and `jq`; on a connected 25.12.5 router both resolve
from the router's own `routing/` and `packages/` feeds (verified: `apk search -x nodogsplash` →
`5.0.2-r2`). **With no uplink they cannot resolve and the install fails** — that is Path 3.

---

## Path 3 — package, WAN-less router (offline bundle) — NOT PUBLISHED YET

Nothing to run today. The bundle (package + full dependency closure + manifest + `install-offline.sh`,
one command from a machine that *does* have internet) is being built now: cards
`OFFLINE-BUNDLE-1..3`. Until it ships, a WAN-less router can only be installed with the installer's
sideload path, which needs a laptop with internet to fetch the dependency packages.

---

## Verification to run on EVERY path (this is what the review club does)

Replace `R=192.168.1.1`.

| # | Gate | Command / check | Pass condition |
|---|---|---|---|
| 1 | **Identity** | `ssh root@$R 'sha256sum /usr/bin/tollgate-wrt'` | equals the payload sha256 of the artifact you installed (pre17 a53: `f3211b42…64a4dd059`) |
| 2 | **Version** | `ssh root@$R 'apk info -v \| grep tollgate-wrt'` | `tollgate-wrt-0.6.0_alpha4_pre17-r1` |
| 3 | **Surfaces** | from a client: `curl -sk -o /dev/null -w '%{http_code}' http://$R:2051/` etc. | `:2051` → 200/403, `:2050` → 404/200, `:2121` → 000 from an unauthenticated br-lan client (LAN-only rule), `:8080` → 307, SSH 22 stays alive |
| 4 | **Policy** | `ssh root@$R 'uci -q show nodogsplash \| grep users_to_router'` and `nft list chain inet fw4 admin_board_input_guard` | list has `22 23 53 67 80 443 2050 2051 2121 8080`, **no `8090`, no `8443`**; the guard chain EXISTS and shows `iifname "br-lan" tcp dport { 8090, 8443 } counter drop` |
| 5 | **Guest-side block** | from a client on the **open** SSID: `curl -sk -o /dev/null -w '%{http_code}' http://$R:8090/` | `000` (not 200) |
| 6 | **Happy path** | join `tollgate-7TJZ` → portal should redirect → buy (Cashu token ≥ 64 sats from an accepted mint, or LN) → gate opens → traffic flows → `/usage` counts | portal loads, payment succeeds, browsing works |

Admin access note: the owner-facing board `:8090` is now reachable **only from the private network**.
Join `c08r4d0r-4462` (the operator SSID) or use LuCI on `:8080`, which stays pre-auth reachable by design.

Capture per path: the artifact name + sha256, the identity read-back, gate 4's raw output, and the
happy-path result. A path is "verified" only when gates 1–6 pass **on that path**.

---

## Order that keeps iterations down

1. Flash to vanilla (Path 0, `--commit`).
2. Bring up an uplink (WAN DHCP or STA) — required for Paths 1 and 2.
3. Path 1 (installer) → run gates 1–6, capture.
4. Flash to vanilla again.
5. Path 2 (package) → run gates 1–6, capture. Remember the `fw4 reload` workaround above.
6. Compare: **policy must be identical**; only the operator choices may differ.
