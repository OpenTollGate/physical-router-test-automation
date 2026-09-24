# Dual-install-path e2e — flash-free dry-run report

Scope: everything the new coverage in `tests/scenarios/test_install_paths.py` can
prove **without flashing** the bench.  The two full flash cycles are the locked
bench phase (§5) and are deliberately left runnable rather than half-run.

* host: this machine (`enp0s31f6`, `192.168.1.200/24`, br-lan side of the bench)
* bench: GL-MT3000 @ `192.168.1.1` — OpenWrt `25.12.5 r33051-f5dae5ece4`,
  `aarch64_cortex-a53`, `mediatek/filogic`, `apk`-only image
* release under test: `FreedomTechFeed/packages v0.6.0-alpha4-pre16`
* command: `scripts/install-path-e2e.py --dry-run --host 192.168.1.1`
* raw JSON: `reports/install-paths/dry-run-20260924T224637Z.json`
* generated table: `reports/install-paths/dry-run-latest.md`

---

## 1. Result

20 checks: **18 pass, 1 fail, 1 informational skip**.

The one failure is a finding, not a harness bug: **the `pre16` artifact does not
ship the `#566` admin-board guard**, so the card's POLICY claim is unreachable
with this release (§4).  Everything else — artifact fetch, hash, identity
reference, installer path, flash image, happy-path suite — is green.

| check | status | detail |
| --- | --- | --- |
| `release-manifest` | pass | 14 entries in `v0.6.0-alpha4-pre16/SHA256SUMS` |
| `artifact-selection` | pass | selected `tollgate-wrt_0.6.0_alpha4_pre16_aarch64_cortex-a53.apk` (fmt=apk, arch=aarch64_cortex-a53) |
| `artifact-sha256-vs-manifest` | pass | `104e9ce00b8f01c0…` (8 361 504 B) == manifest |
| `expected-binary-sha256-from-artifact` | pass | `/usr/bin/tollgate-wrt` inside the `.apk`: `dce8b1f1c89a0d04…` |
| `artifact-ships-the-566-policy` | **fail** | payload has no `31-admin-board-not-guest-reachable.nft` |
| `sibling-ipk-binary-is-a-different-build` | pass | apk payload `dce8b1f1…` ≠ ipk payload `5ddda42b…` |
| `package-format-split` | pass | apk magic `ADBd`, ipk magic `\x1f\x8b\x08\x00` |
| `installer-script-fetched` | pass | 30 462 B, `bash -n` clean, `--tag` supported |
| `installer-command-shape` | pass | `bash <(curl -fsSL <raw>) --tag v0.6.0-alpha4-pre16 192.168.1.1 '<pw>' '<ln>'` |
| `fresh-flash-image-verified` | pass | `openwrt-25.12.5-mediatek-filogic-glinet_gl-mt3000-squashfs-sysupgrade.bin`, sha256 ok |
| `happy-path-suite-collected` | pass | 6 tests listed for the reused suite (repo-local runner) |
| `bench-lock-state` | pass | HELD by another card's run at both snapshots (§5) |
| `bench-port-22/2050/2051/2121/8080` | pass | open from this host |
| `bench-port-8090` | pass (open) | see §4 — currently guest-reachable |
| `bench-identity` | pass | `OpenWrt 25.12.5 … \| installed: tollgate-wrt-0.6.0_alpha4_pre16-r1` |
| `bench-policy-snapshot` | skip (informational) | current install does not match the expected policy (§4/§5) |

Full JSON (every check plus the collected facts) is committed at
`reports/install-paths/dry-run-20260924T224637Z.json`.

---

## 2. Executed evidence (raw)

### 2.1 Release manifest ↔ artifact bytes

```
$ gh release view v0.6.0-alpha4-pre16 --repo FreedomTechFeed/packages
asset: SHA256SUMS
asset: SHA256SUMS.sig
asset: tollgate-wrt_0.6.0_alpha4_pre16_aarch64_cortex-a53.apk
asset: tollgate-wrt_0.6.0_alpha4_pre16_aarch64_cortex-a53.ipk   (+6 more arch pairs)

$ grep aarch64_cortex-a53 SHA256SUMS
104e9ce00b8f01c09840c9aa6d8976c6a6712a4cd05376ddf5f5a237eb2b4e72  tollgate-wrt_0.6.0_alpha4_pre16_aarch64_cortex-a53.apk
b90e9966dfb485b8d601144bada9b411711d3d98c1a78444360ddac997a052c4  tollgate-wrt_0.6.0_alpha4_pre16_aarch64_cortex-a53.ipk

$ sha256sum ~/.cache/prta-install-paths/v0.6.0-alpha4-pre16/*.apk
104e9ce00b8f01c09840c9aa6d8976c6a6712a4cd05376ddf5f5a237eb2b4e72  …a53.apk        # manifest match
```

### 2.2 The identity gate's reference value, derived from the artifact

```
$ apk.static extract --allow-untrusted --destination /tmp/x …a53.apk
/tmp/x/usr/bin/tollgate-wrt
$ sha256sum /tmp/x/usr/bin/tollgate-wrt
dce8b1f1c89a0d04d705aa4ed15071aaf66a658dda791dee0996f99c76fd56bb   (12 242 208 B, BuildID 6dd3eb17…)

$ tar -xzOf …a53.ipk ./data.tar.gz | tar -xzO ./usr/bin/tollgate-wrt | sha256sum
5ddda42bf55c3e007ce01016d9aa076661307e604c8df4e3b4d6e11c86c21956   (12 295 456 B, BuildID 46ea257b…)
```

**Every non-Go file is byte-identical between the two formats** (`99-tollgate-setup`,
`90-tollgate-captive-portal-symlink`, `20-nds-enforce.nft`, `check_package_path`,
`lib/upgrade/keep.d/tollgate` all match); only the two Go binaries differ.  The
`.apk` is produced by the OpenWrt SDK job, the `.ipk` by `packaging/build-ipk.sh`;
their ELF section order differs (the SDK's packaging strips the payload).
⇒ the identity gate must use the artifact of the format it installs — cross-format
comparison is wrong in both directions.

### 2.3 Format split (this is the `.ipk` rejection proof, off-router half)

```
$ file …a53.apk        → data
$ xxd …a53.apk | head -1
00000000: 4144 4264 …                      ("ADBd" — apk v3, not a tar)
$ tar -tzf …a53.apk
tar: This does not look like a tar archive        (exit 2)

$ file …a53.ipk        → gzip compressed data
$ tar -tzf …a53.ipk    → ./debian-binary ./data.tar.gz ./control.tar.gz
```

The *on-router* half — `apk add --allow-untrusted <the .ipk>` returning
`ERROR: v2 package format error`, exit `99` — is implemented as
`test_a3_ipk_is_rejected_on_an_apk_image` and is **deferred to the locked phase**
(§5).

### 2.4 Installer path — command shape verified against the real script

```
$ curl -fsSL https://raw.githubusercontent.com/OpenTollGate/tollgate-installer/main/install-and-test.sh -o /tmp/i.sh
$ sha256sum /tmp/i.sh    531c0754e9e133496bc209ffd80a2e4c8bc262437e93b05eea41eaca1ae14ed7   (30 462 B)
$ bash -n /tmp/i.sh      → exit 0
$ grep -n -- '--tag' /tmp/i.sh
 66: # Override with --tag / --channel, or TOLLGATE_FEED_RELEASE_TAG …
109:        --tag)     FEED_TAG_OVERRIDE="${2:-}"; shift 2 ;;
$ grep -n 'FEED_REPO=' /tmp/i.sh
 68: FEED_REPO="FreedomTechFeed/packages"      # same feed the artifact comes from
```

Built command (the canonical form, `--tag` pinned to the release under test):

```
bash <(curl -fsSL https://raw.githubusercontent.com/OpenTollGate/tollgate-installer/main/install-and-test.sh) \
     --tag v0.6.0-alpha4-pre16 192.168.1.1 '<router-password>' '<ln-address>'
```

**Note for the card:** the installer's `--tag` is on upstream `main` only.  The
local clone at `/home/c03rad0r/repos/tollgate-installer` (HEAD `6a17dd4`) still
has the older positional-only script; pointing `TOLLGATE_INSTALLER_URL` at that
copy makes `test_b1` fail its `--tag` check by design.

### 2.5 Fresh-flash prerequisite

```
$ sha256sum ~/worktrees/mt3000-flash/openwrt-25.12.5-mediatek-filogic-glinet_gl-mt3000-squashfs-sysupgrade.bin
1ffa6526ea099878e0fc520dc0473e95202c1a470b3eabcd2cd40e9c8eaab8c6
$ curl -s .../25.12.5/targets/mediatek/filogic/sha256sums | grep gl-mt3000-squashfs-sysupgrade
1ffa6526ea099878e0fc520dc0473e95202c1a470b3eabcd2cd40e9c8eaab8c6 *openwrt-25.12.5-mediatek-filogic-glinet_gl-mt3000-squashfs-sysupgrade.bin
$ python3 scripts/fresh-flash.py --check
image  : …/openwrt-25.12.5-mediatek-filogic-glinet_gl-mt3000-squashfs-sysupgrade.bin
sha256 : OK
wallet : total_sats=… (see §6 — not probed in this dry run; --check does probe)
router : OpenWrt 25.12.5 r33051-f5dae5ece4|aarch64_cortex-a53|mediatek/filogic
```

`sysupgrade -n` is the only mode the harness issues; the flash itself is gated by
`TOLLGATE_ENABLE_SYSUPGRADE_FLASHING=true` **and** the wallet gate (§6).

### 2.6 Happy-path suite (reused, collectable, runner resolved correctly)

```
$ node_modules/.bin/playwright test --config=playwright.config.mjs --list \
    --project=desktop-portal --grep "captive portal — happy path" protocol/captive-portal.spec.mjs
Listing tests:
  [desktop-portal] › protocol/captive-portal.spec.mjs:88 › captive portal — happy path › API returns valid advertisement with pricing
  [desktop-portal] › protocol/captive-portal.spec.mjs:95 › captive portal — happy path › portal shows cashu token input
  [desktop-portal] › protocol/captive-portal.spec.mjs:101 › captive portal — happy path › portal shows lightning amount input
  [desktop-portal] › protocol/captive-portal.spec.mjs:107 › captive portal — happy path › portal shows mint selection pricing buttons
  (+2 more in the same spec)
```

`npm install` was needed on this host (no `node_modules/`); a *global*
`playwright` CLI is not enough (`unknown command 'test'`) — the harness now
resolves `node_modules/.bin/playwright` and says exactly that when it is missing.

---

## 3. What the harness adds, proven off-router

| area | proven by |
| --- | --- |
| manifest parsing, arch/format selection, opkg skip reason | `tests/unit/test_install_paths.py` (42 cases) |
| artifact sha256 check | dry-run check + unit tests |
| identity-gate reference from the *same-format* artifact (apk + ipk readers, wrong-format rejection) | unit tests + real `pre16` artifact |
| POLICY parser/gate: allow-set equality, forbidden `8090/8443`, guard file + `br-lan` drop, `:8090` unreachable, SSH alive | unit tests against a verbatim `uci show nodogsplash` capture (§4) |
| surfaces gate (`:2051/:2050/:2121` 200, `:8080` 307, `:8090` 000) | unit tests |
| happy-path report parser: 0 tests / failures / skips / missing titles | unit tests |
| flash guard (refuse non-empty wallet), setup-marker trap, image validation | `tests/unit/test_fresh_flash.py` |
| bench lock: flock semantics, holder identity in the error, **separate-process** negative control, lock released when the holder is killed | `tests/unit/test_bench_lock.py` |

The parser was validated against a real capture from the bench, which also shows
the *pre-fix* shape the policy gate exists to catch:

```
$ ssh root@192.168.1.1 'uci show nodogsplash | grep users_to_router'
nodogsplash.@nodogsplash[0].users_to_router='allow tcp port 22' 'allow tcp port 23' 'allow tcp port 53' \
 'allow udp port 53' 'allow udp port 67' 'allow tcp port 443' 'allow tcp port 2121' 'allow tcp port 2050' \
 'allow tcp port 2051' 'allow tcp port 80' 'allow tcp port 8080' 'allow tcp port 8090'
                                    ↑ the forbidden port, from the PRE-#566 writer
$ ssh root@192.168.1.1 "nft list ruleset | grep -n -A1 8090"
257:            tcp dport 8090 counter packets 3 bytes 180 accept
$ ssh root@192.168.1.1 'ls /etc/nftables.d/'
10-custom-filter-chains.nft  20-nds-enforce.nft  30-backend-firewall.nft  README
                                    ↑ no 31-admin-board-not-guest-reachable.nft
$ curl -s -o /dev/null -w '%{http_code}' -m 6 http://192.168.1.1:8090/   → 200
```

⇒ parsed allow-set = `{22,23,53,67,80,443,2050,2051,2121,8080,8090}`; the gate
reports exactly one violation (`forbidden ports [8090]`) plus the missing guard.

---

## 4. Finding: `pre16` cannot satisfy the card's POLICY claim

Verified facts:

1. The `pre16` binary reports version `0.6.0-alpha4-g2796d96`; commit `2796d96`
   **is** a descendant of the `#566` fix `542f6f47` (`fix(packaging): make the
   :8090 admin board unreachable from the guest network`):
   ```
   $ git merge-base --is-ancestor 542f6f47 2796d96 && echo YES
   YES
   $ git show 2796d96:packaging/files/etc/nftables.d/31-admin-board-not-guest-reachable.nft | head -1
   (file exists at that commit)
   $ git show 2796d96:packaging/Makefile | sed -n '208,209p'
     $(INSTALL_DIR) $(1)/etc/nftables.d
     $(INSTALL_DATA) $(sort $(wildcard $(PKG_MAKEFILE_DIR)files/etc/nftables.d/*.nft)) $(1)/etc/nftables.d/
   ```
2. The **published** `.apk` (and `.ipk`) nevertheless do **not** contain that
   file:
   ```
   $ ls /tmp/x/etc/nftables.d/
   20-nds-enforce.nft  30-backend-firewall.nft
   ```
   while its `99-tollgate-setup` (sha `f708557d…`, ≠ the `2796d96` blob
   `1007d06a…`) *does* contain
   `uci -q del_list nodogsplash.@nodogsplash[0].users_to_router='allow tcp port 8090'`.
3. The bench, which currently runs `tollgate-wrt-0.6.0_alpha4_pre16-r1`, matches
   that artifact: no guard file, `:8090` accepted in the ruleset, `:8090` answers
   200 from a br-lan client, and `allow tcp port 8090` still in
   `users_to_router`.

**Consequence for `t_a05094ad`:** "a bare package install applies the policy
(allow list, `:8090`/`:8443` removal, nft guard)" is **not true for `pre16`** —
the artifact ships neither the guard file nor a first-install run that removes
`8090` from the allow list on a previously-configured router.  The gate needs a
release whose payload actually carries the guard (the card's `pre17`).  The new
`test_a2b_artifact_ships_the_policy_material` fails this *before* any router time
is spent, naming the missing file.

Second-order detail worth keeping: the `8090` removal lives in the *full setup*
branch of `99-tollgate-setup`; on a same-version reinstall the marker
`/etc/tollgate-setup-done` short-circuits it.  That is why the card's procedure —
and this harness — measure after a **fresh flash**, and why the harness refuses a
"fresh" image that already carries the marker.

---

## 5. Deferred to the locked bench phase (and why)

The bench was **not free** during this dry run.  Both snapshots found the lock
held by another card's run:

```
# snapshot 1
manager pid=2584919 purpose=curl|bash-pre16-validation since=2026-09-25T00:19:40+02:00
# snapshot 2 (lock now free, taken over)
manager pid=2721026 purpose=policy-convergence-ab t_3ac1bb9d since=2026-09-25T00:45:20+02:00
```

Between the two dry runs the router's own state changed (`users_to_router` lost
most of its entries and `:8090` behaviour moved), i.e. **another worker was
mutating the very state this task measures** — the exact failure mode
`t_aa94ad3b` exists to prevent.  Nothing state-changing was attempted here.

Deferred, runnable, documented:

| step | runner | gate |
| --- | --- | --- |
| on-router `.ipk` rejection proof (`v2 package format error`, exit 99) | `test_a3_ipk_is_rejected_on_an_apk_image` | bench lock |
| flash cycle A + direct `.apk` install + gates 1–5 | `pytest …/test_install_paths.py -k TestDirectPackageInstall --no-deploy` | lock + `TOLLGATE_ENABLE_SYSUPGRADE_FLASHING=true` + empty wallet |
| flash cycle B + installer path + gates 1–5 + parity | `pytest …/test_install_paths.py -k TestInstallerPath --no-deploy` | same, plus `TOLLGATE_LN_ADDRESS` |
| policy-parity comparison (choices-only diff) | `test_b6_policy_is_identical_to_the_package_only_install` | both cycles |

One-shot: `TOLLGATE_LN_ADDRESS=you@coinos.io python3 scripts/install-path-e2e.py --flash-and-run`.

---

## 6. Wallet gate — not exercised (no flash attempted)

`scripts/fresh-flash.py --check` and `TestFreshFlashPrerequisite::test_02` probe
`tollgate --json wallet balance` plus non-empty files under
`/etc/tollgate/ecash`, and **refuse** to flash unless the operator has drained
(`tollgate wallet drain cashu --yes`) or passes `--allow-nonempty-wallet`.  The
refusal path, the drain-command message and the "zero JSON total but proofs on
disk" case are unit-tested; no wallet probe result is claimed here because the
flash step was never reached and the bench was owned by another run.

---

## 7. Reproduction

```bash
cd /home/c03rad0r/repos/physical-router-test-automation
git checkout pr/dual-install-path-e2e
npm install                                     # @playwright/test for the reused suite
TOLLGATE_APK_TOOL=/path/to/apk.static \
TOLLGATE_SSH_PASSWORD=… TOLLGATE_LN_ADDRESS=you@coinos.io \
python3 scripts/install-path-e2e.py --dry-run --host 192.168.1.1 \
  --md-out docs/install-paths-dry-run-report.md

python3 -m pytest tests/unit/test_install_paths.py tests/unit/test_fresh_flash.py \
  tests/unit/test_bench_lock.py -q
```

`apk.static` (for reading apk v3 artifacts on the test host):

```bash
curl -fsSL -o /tmp/apk-tools.apk \
  https://dl-cdn.alpinelinux.org/alpine/edge/main/x86_64/apk-tools-static-3.0.8-r0.apk
mkdir -p /tmp/apktools && tar -xzf /tmp/apk-tools.apk -C /tmp/apktools sbin/apk.static
export TOLLGATE_APK_TOOL=/tmp/apktools/sbin/apk.static
```
