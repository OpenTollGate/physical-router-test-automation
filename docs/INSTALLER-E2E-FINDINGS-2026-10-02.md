# Installer E2E on physical routers — findings, 2026-10-02

Run: three routers cabled to one host via USB-C→Ethernet adapters; driven through
the **released** `tollgate-installer` (installer build `v0.6.0-alpha2-rc3`
`dd77c3b`) pinned to feed `v0.6.0-alpha4-pre21` (module pin `74cd6f9`).

## Verdict

**The installer is not release-ready against 16 MB-flash devices.** It walks the
operator through the whole flow and fails at the last step, *after* it has
already rotated the router's root password.

## 1. Hard blocker — installed footprint exceeds 16 MB-class flash by ~3×

Measured on a GL.iNet GL-AR300M16 (16 MB NOR, OpenWrt 24.10.0, `mips_24kc`),
after the installer had staged and pushed the package:

```
Installing tollgate-wrt (0.6.0_alpha4_pre21-r1) to root...
Collected errors:
 * verify_pkg_installable: Only have 8016kb available on filesystem /overlay,
   pkg tollgate-wrt needs 23850
 * opkg_install_cmd: Cannot install package tollgate-wrt.
```

- package `tollgate-wrt_0.6.0_alpha4_pre21_mips_24kc.ipk` = 8,921,046 B
  (`data.tar.gz` = 8,930,225 B; **uncompressed installed need = 24,422,400 B**)
- device `/overlay` = 9.4 MB total, **8,016 KB free** at install time
- deficit ≈ **15.8 MB** — not marginal

Same shape on the Cudy WR3000 v1 (16 MB flash, OpenWrt 25.12.5, `aarch64_cortex-a53`):
- package `.apk` = 8,500,975 B, `/overlay` = 5.9 MB total, **4.8 MB free**

So the RELEASES matrix publishes `mips_24kc/ath79-generic` and
`aarch64_cortex-a53/mediatek-filogic` for *any* device reporting that tuple,
but the payload cannot be installed on the smallest flash class in that tuple.
Only MT3000-class (NAND) devices can host it.

### The defect is the ORDER, not the size

The correct behaviour when the target cannot hold the payload is to refuse
**before** changing anything. Today the wizard:

1. generates a new root password and applies it,
2. then installs, and fails,
3. then displays "This deploy failed AFTER the router's root password had
   already been generated and set. It is shown ONCE and cannot be recovered."

The operator is left with a rotated credential and nothing installed. A free-space
pre-flight (e.g. compare the package's `Installed-Size`/unpacked need against
`statvfs("/overlay")` before the credential step) turns this into a clean refusal.

## 2. Defect — discovery is nondeterministic across interfaces

Two scans **of the same host, minutes apart**, disagreed:

- run A offered `"GL-AR300M16 — OpenWrt 24.10.0 … (192.168.11.1)"`
- run B did **not** offer `192.168.11.1` at all; the wizard's own first scan
  missed the router, and the driver only recovered by clicking the wizard's
  Rescan control (5 retries).

An operator sees "no routers found" for a router that is cabled and answering.
Serial runs are needed to quantify the miss rate; the retry belongs in the
wizard, not in the operator's head.

## 3. Defect — an address clash hides a router completely

An AR300M16 and an MT3000 were both on `192.168.1.1`, on two different adapters.
Because discovery and the host-key store are keyed by **IP only**, the second
device is invisible and the two devices' host keys are indistinguishable from an
impersonation attempt (the same condition as the 2026-09-28 incident and
installer PR #66).

Working fix used here: renumber the accessible device
(`uci set network.lan.ipaddr=192.168.11.1; uci commit network`) so every router
in the rig has a distinct address. After that one scan listed all three
correctly, with real model + firmware strings.

## What the installer got RIGHT (verified from live deploy output)

- feed release resolution → `v0.6.0-alpha4-pre21`, module pin `74cd6f9`
- package sourced from the feed release and **cross-checked against SHA256SUMS
  and the GitHub release API**
- package staged **laptop-side** and pushed over SSH (8711 KB, "used from cache")
- dependency fallback: when the router's own `opkg update` failed, the installer
  downloaded `nodogsplash` (42 KB) and `jq` (419 KB) laptop-side, pushed them,
  and they installed cleanly
- correct arch/lane selection (`mips_24kc` → `.ipk` for an opkg router;
  `aarch64_cortex-a53` → `.apk` for a 25.12 apk router)
- **precise, honest failure reporting**, and the one-shot credential is shown
  even on a failed deploy
- captive-portal enforcement does arm on the 25.12 lane: with the main package
  absent, nodogsplash was already intercepting
  (`http://<router>/` → `307 → http://<router>:2050/splash.html?redir=…`)

## Environment caveats

- **No Cashu funds** in any wallet on the test host, so the LN+Cashu paid path
  could not be paid with real money. The rig for a *real* paid flow without real
  funds is present and was brought up:
  `deployment-kit/scripts/bring-up-fakewallet-mint.sh`
  (cdk-mintd 0.18.0 fakewallet, auto-settles NUT-04 quotes) at
  `http://<host>:3338`, plus `configure-router-test-mint.sh` to point a router
  at it. Neither could be used, because both routers whose password we hold had
  no room for the package.
- **MT3000 in the rig, already running TollGate**, root password not recoverable
  from this session: `:2050`, `:2051`, `:2121` all answering.

## Reproduce

```bash
# 1. trust the routers' host keys from their OWN console (out of band)
ssh root@<router> 'dropbearkey -y -f /etc/dropbear/dropbear_ed25519_host_key'
ssh-keyscan -t ed25519 <router> >> ~/.tollgate-known-hosts

# 2. run the released installer pinned to the feed release under test
TOLLGATE_FEED_RELEASE_TAG=v0.6.0-alpha4-pre21 ./tollgate-installer -port 8099

# 3. drive the wizard UI with video
TG_ROUTER_IP=<router> TG_LNURL=<addr> TG_OUT=<dir> node tests/installer-wizard-run.mjs

# 4. the free-space gate, by hand
ssh root@<router> 'opkg install /tmp/tollgate-wrt.ipk'   # opkg/ipk lane
ssh root@<router> 'df -h /overlay'
```

## 4. Second lane — same wall, and it is the BINARY, not the total

Cudy WR3000 v1, OpenWrt 25.12.5, `aarch64_cortex-a53`, apk lane:

```
(1/1) Installing tollgate-wrt (0.6.0_alpha4_pre21-r1)
  Executing tollgate-wrt-0.6.0_alpha4_pre21-r1.pre-install
ERROR: tollgate-wrt-0.6.0_alpha4_pre21-r1: failed to extract usr/bin/tollgate-wrt: No space left on device
ERROR: tollgate-wrt-0.6.0_alpha4_pre21-r1: No space left on device
1 error; 19.4 MiB in 181 packages
```

4.6 MB free; the **Go binary alone** does not fit. So on both package managers,
on both arches, in both directions, the payload loses to the flash.

## 5. Defect — a FAILED install leaves the router half-migrated

After the failure the device is not unchanged. On the Cudy:

- `uhttpd.main.listen_http` moved from `0.0.0.0:80` to **`0.0.0.0:8080`**
- `uhttpd.main.commonname` set to `TollGate`
- `/etc/tollgate/install.json` written
- the `/etc/uci-defaults/` marker consumed (directory empty)

Consequences an operator hits immediately:

1. the admin UI **silently moves off `:80`** to `:8080`/`:443` while nothing is
   installed — `http://<router>/` simply stops answering (000);
2. because the setup marker was consumed, a **retry takes a different path** and
   skips the uci-defaults stage that a fresh device would run.

`/etc/tollgate/install.json` on the test unit carried `install_time`
`1790897522` — **91 minutes before this run**, i.e. the device had already been
through a failed installer attempt and was handed to this run already
half-migrated. A rollback that only removes packages (what was done here to
restore the bench) does **not** restore the uhttpd config or the marker.

Rolled-back state, verified: deps purged (`apk del`, world entry for
`tollgate-wrt` removed first — the aborted install left it in `/etc/apk/world`,
which makes a plain `apk del <dep>` fail to resolve), no nds/tollgate init
scripts, no tollgate/nodogsplash nft references, admin answering on `:8080`
(200) and `:443` (403 = auth required, normal). The GL-AR300M16 control was
restored fully stock: deps gone, `listen_http` back on `:80`, `200` with no
redirect, no `/etc/tollgate`.

## 6. Harness defect (ours, disclosed)

`tests/installer-wizard-run.mjs` first reported `VERDICT=TIMEOUT` for a deploy
that had plainly failed: the terminal-state matcher looked for `failed:` with a
colon, but the wizard renders "Setup failed" / "Package installation failed".
Fixed here to match both. A harness that cannot tell "failed" from "still
running" would have hidden exactly the result this run exists to surface.
