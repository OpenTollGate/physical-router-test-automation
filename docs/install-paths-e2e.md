# Dual-install-path end-to-end coverage (fresh flash → install → happy path)

This is the evidence gate for the review-club card **"REVIEWER GUIDE: package-only
install — policy identical, choices differ"** (`t_a05094ad`, board
`tollgate-module-basic-go`).  It answers one question mechanically:

> after a **fresh flash**, does a **bare package install** produce the same
> *policy* as the **installer** path, and does the **happy path** actually work
> in both cases?

* scenarios: `tests/scenarios/test_install_paths.py`
* flash-free runner: `scripts/install-path-e2e.py --dry-run`
* fresh-flash prerequisite: `scripts/fresh-flash.py`
* unit coverage: `tests/unit/test_install_paths.py`, `test_fresh_flash.py`,
  `test_bench_lock.py`

---

## 1. What the two scenarios do

Both start from a **freshly flashed** OpenWrt 25.12.x image and end with the same
five gates.  Only the install step differs.

| # | gate | assertion |
|---|---|---|
| 1 | **artifact identity** | `sha256sum /usr/bin/tollgate-wrt` on the router **equals** the sha256 of that binary *inside the artifact that was installed* (the same-format artifact — see §4) |
| 2 | **version** | the installed package version string contains the artifact's version stem |
| 3 | **surfaces** | `:2051` → 200 (splash/SPA), `:2050` → 200 (stub), `:2121` → 200 (API), `:8080` → 307 (LuCI → portal), and SSH stays alive (port 22 still in the NDS allow list) |
| 4 | **POLICY** | `uci show nodogsplash | grep users_to_router` **set-equals** `{22,23,53,67,80,443,2050,2051,2121,8080}`, `8090`/`8443` are **absent**, `/etc/nftables.d/31-admin-board-not-guest-reachable.nft` is present and drops from `br-lan`, and `:8090` is **not reachable** from a `br-lan` client |
| 5 | **happy path** | the *existing* Playwright suite passes (see §3) |

**Scenario A — direct package install**

1. `test_a1` select the published artifact by the router's **detected package
   manager** (apk on 25.x ⇒ `.apk`; opkg on ≤ 24.10 ⇒ `.ipk`).  The opkg case is
   an **explicit skip with the reason**, never a silent pass.
2. `test_a2` verify the artifact's sha256 against the release `SHA256SUMS`.
3. `test_a2b` **flash-free**: the payload must actually ship the guard file *and*
   a setup script that removes `8090`/`8443` from `users_to_router`.  If the
   release does not ship them the policy gate is unreachable, and the failure is
   reported *here*, with the reason (see §6).
4. `test_a3` push the **`.ipk`** and prove `apk` rejects it on an apk-only image
   (`ERROR: v2 package format error`, exit `99`).  This is what makes the
   selection in step 1 meaningful.
5. `test_a4` push the `.apk` (sha checked again on the router) and
   `apk add --no-check-certificate --allow-untrusted`.
6. `test_a5`–`test_a8` gates 1–5.

**Scenario B — the canonical installer path**

1. `test_b1` fetch `install-and-test.sh` **from its repo URL**, `bash -n` it,
   assert it advertises `--tag`, then build the canonical command
   (Felix's preferred test form):
   `bash <(curl -fsSL <raw url>) --tag <tag> <router> <password> <ln-address>`
2. `test_b2` run it; require exit 0 **and** `=== DEPLOY COMPLETE ===`; never
   call a partial run a pass.
3. `test_b3`–`test_b5` gates 1–5.
4. `test_b6` **policy parity**: the two snapshot dicts must agree on the policy
   components; the only tolerated difference is the operator *choices*
   (hostname, guest SSID, LN address) — the card's actual claim.

Every run writes `install-paths-evidence.json` (raw outputs, hashes, snapshots)
next to the pytest temp dir, or to `$TOLLGATE_INSTALL_PATHS_EVIDENCE`.

---

## 2. Running it

### 2.1 Flash-free (safe, no router state change)

```bash
# everything that does not need a flash; writes reports/install-paths/
TOLLGATE_APK_TOOL=/path/to/apk.static \
python3 scripts/install-path-e2e.py --dry-run --host 192.168.1.1 \
  --md-out docs/install-paths-dry-run-report.md
```

Checks: release manifest → artifact selection → artifact sha256 → expected
binary sha256 **from the artifact** → payload policy readiness → apk/ipk format
split → installer script fetched/`--tag`/command shape → flash image verified →
happy-path suite collectable → bench lock state → read-only bench ports/identity/
policy snapshot (`--skip-probe` to leave the bench untouched entirely).

Exit code is non-zero when a check fails, so it can be a CI/preflight gate.

### 2.2 The full locked bench phase (two flash cycles)

```bash
# one-time: the reused happy-path suite needs @playwright/test
npm install

# preconditions (read-only) — refuses on a non-empty wallet
python3 scripts/fresh-flash.py --check

# drain is the OPERATOR's step (real money):
#   ssh root@192.168.1.1 'tollgate wallet drain cashu --yes'   # prints the tokens

# flash cycle A
python3 scripts/fresh-flash.py --flash --yes-i-mean-it --readdress enp0s31f6

TOLLGATE_SSH_HOST=192.168.1.1 TOLLGATE_SSH_PASSWORD=... \
TOLLGATE_LN_ADDRESS=you@coinos.io \
python3 -m pytest tests/scenarios/test_install_paths.py --no-deploy -v --timeout=3600

# flash cycle B: flash again (the harness refuses a stale/dirty image state),
# then re-run the same pytest command.
```

or, in one shot:

```bash
TOLLGATE_LN_ADDRESS=you@coinos.io python3 scripts/install-path-e2e.py --flash-and-run
```

`--no-deploy` is **required**.  The session-scoped `deploy_session` fixture
otherwise rewrites the mint list (`replace_mints`), enables the debug portal and
restarts the backend — that is not a clean install, and it would invalidate the
"policy as installed" claim.  The module fails loudly if the option is missing.

### 2.3 Environment knobs

| var | meaning |
|---|---|
| `TOLLGATE_FEED_TAG` / `TOLLGATE_FEED_VERSION` | release to test (default `v0.6.0-alpha4-pre16` / `0.6.0_alpha4_pre16`) |
| `TOLLGATE_ARTIFACT_ARCH` | arch to fetch (default `aarch64_cortex-a53`) |
| `TOLLGATE_ARTIFACT_DIR` | artifact cache (default `~/.cache/prta-install-paths/<tag>`) |
| `TOLLGATE_APK_TOOL` | `apk.static` able to `extract` (see §4) |
| `TOLLGATE_LN_ADDRESS` | required for scenario B (the installer's operator choice) |
| `TOLLGATE_INSTALLER_URL` | override the installer script URL |
| `TOLLGATE_HAPPY_PATH_ALLOW_SKIP` | `1` downgrades skipped happy-path tests to a warning (default: skip = fail) |
| `TOLLGATE_CAPTIVE_PORTAL_PORT` | portal port for the UI suite (default `2051`) |
| `TOLLGATE_BENCH_LOCK` | bench lock path override |
| `TOLLGATE_ALLOW_NONEMPTY_WALLET` | `1` accepts losing the ecash on a non-empty wallet |
| `TOLLGATE_ENABLE_SYSUPGRADE_FLASHING` | `true` allows the flash step |
| `TOLLGATE_FRESH_FLASH_IMAGE` | image path (default: the pre-downloaded copy under `~/worktrees/mt3000-flash/`) |

---

## 3. The happy path is reused, not reinvented

The UI flow driven is the **existing** suite:

* spec — `tests/protocol/captive-portal.spec.mjs`
* describe — `captive portal — happy path` (4 tests: advertisement + pricing,
  cashu token input, lightning amount input, mint pricing buttons)
* the same spec that `make test-captive-portal-happy` runs
  (`config/make-pytest-map.yaml` → `pytest: tests/protocol/captive-portal.spec.mjs`)

Command actually executed (cwd `tests/`, runner resolved to
`node_modules/.bin/playwright`, **not** a global CLI):

```
<repo>/node_modules/.bin/playwright test --config=playwright.config.mjs \
  --project=desktop-portal --grep "captive portal — happy path" \
  protocol/captive-portal.spec.mjs
```

The Playwright JSON report (`tests/report/report.json`) is parsed by
`lib.install_paths.parse_happy_path_report`, which fails on **0 tests collected**
(an empty run is not a pass), on any failure, on any expected test missing, and
— by default — on any **skip** (a skipped happy path is not a completed happy
path; on a degraded router the suite skips itself, and that must be visible).
`TOLLGATE_HAPPY_PATH_ALLOW_SKIP=1` records skips as a warning instead.

---

## 4. Traps this harness encodes (found the hard way)

1. **The `.apk` and `.ipk` of one release do not carry the same binary.**
   Verified on `v0.6.0-alpha4-pre16`: `.apk` payload `usr/bin/tollgate-wrt` is
   12 242 208 B / `dce8b1f1…`, the `.ipk` payload is 12 295 456 B /
   `5ddda42b…` (identical for every non-Go file; the two are built by different
   CI jobs — the `.apk` through the OpenWrt SDK, the `.ipk` by
   `packaging/build-ipk.sh`).  **Consequence:** the identity gate must derive its
   expected hash from the artifact of the *same format it installs*.  Comparing
   the installed binary against the sibling format's hash produces a false
   failure — or, worse, can hide a real swap.
2. **apk v3 artifacts are `ADBd`-prefixed, not gzipped tars.**  `tar` cannot read
   them; unpacking needs apk-tools (`apk extract --allow-untrusted
   --destination DIR file.apk`).  Install Alpine's `apk-tools-static` and point
   `TOLLGATE_APK_TOOL` at `apk.static` (the harness never guesses).
3. **`npx playwright test` only works with the repo's `@playwright/test`.**  A
   global `playwright` install has no `test` command (`unknown command 'test'`).
   Run `npm install`; the harness resolves `node_modules/.bin/playwright` and
   fails with that instruction rather than a confusing CLI error.
4. **The setup marker trap.**  `99-tollgate-setup` only *re-converges* the NDS
   allow list on a full setup; on a same-version reinstall it merely verifies
   the wireless APs.  So a reinstall can inherit a stale `users_to_router` list
   (including `8090`) and look "unchanged".  The whole point of measuring after
   a **fresh flash** (no `/etc/tollgate-setup-done`) is to exercise the real
   first-install path; the harness asserts the marker is absent on the fresh
   image and refuses a "fresh" image that already carries it.
5. **The bench router drops ICMP.**  Never `ping` it; probe TCP
   (`22/2050/2051/2121/8080/8090`).
6. **`scp` needs `-O` and `apk`/`wget` need `--no-check-certificate`** on this
   image (no `sftp-server`, no CA store).

---

## 5. The bench is single-owner: the lock is not optional

The MT3000 (`192.168.1.1`) is shared by several cards.  An earlier cron
re-installed an old build every 10 minutes and invalidated a day of runs; a
stray deploy loop reverted the `:8090` guard three times in one evening
(`t_aa94ad3b`).

* every router-touching test here runs under `lib.bench_lock`
  (`~/.hermes/state/bench-mt3000.lock`, `flock` + a holder identity line);
* if the lock is held, the module **skips**, and the skip message contains the
  holder's identity line (profile, pid, task, purpose, since);
* the `flock` is the authority — the kernel releases it when the holder dies, so
  a crashed worker cannot wedge the bench;
* a script that only *reads* (probes, `--dry-run`) still reports the lock state.

```bash
python3 -m lib.bench_lock status            # free? who holds it?
python3 -m lib.bench_lock check  || echo busy
flock -n ~/.hermes/state/bench-mt3000.lock -c './your-router-script.sh'
```

---

## 6. Draining before a flash is the operator's step (real money)

`sysupgrade -n` wipes `/etc/tollgate` — `config.json`, `identities.json`,
`install.json` **and `/etc/tollgate/ecash`**.  On an *upgrade* or *reinstall* the
package keeps `/etc/tollgate` (conffiles) and the ecash survives; only a firmware
flash takes it.  Therefore:

* `scripts/fresh-flash.py` probes `tollgate --json wallet balance` **and**
  non-empty files under `/etc/tollgate/ecash`;
* if either says "money", it **refuses** to flash and prints the drain command:
  `tollgate wallet drain cashu --yes` (exit 2 = cancelled, nothing moved);
* `--allow-nonempty-wallet` is the only way past, and it is explicit;
* the same gate runs as `TestFreshFlashPrerequisite::test_02`, so the pytest
  phase cannot flash a wallet that still holds ecash.

Never suggest `rm -rf /etc/tollgate` without draining first.

---

## 7. Known state of the release under test (`v0.6.0-alpha4-pre16`)

The flash-free run **fails** `artifact-ships-the-566-policy`, and that is the
point: the published `pre16` artifact does **not** ship
`/etc/nftables.d/31-admin-board-not-guest-reachable.nft` (its
`/etc/nftables.d/` contains only `20-nds-enforce.nft` and
`30-backend-firewall.nft`), even though the module tree at the commit the binary
reports (`0.6.0-alpha4-g2796d96`, and its `packaging/Makefile` installs
`nftables.d/*.nft` by wildcard) does contain it.  The artifact's
`99-tollgate-setup` does carry the `del_list … 'allow tcp port 8090'/'8443'`
lines, but with no guard file the `:8090` drop cannot be in place.

So the card's policy claim (guard present, `8090`/`8443` absent) is **not
reachable with `pre16`** and needs a release whose payload actually ships the
guard — the `pre17` the card mentions.  Re-run with:

```bash
TOLLGATE_FEED_TAG=v0.6.0-alpha4-pre17 TOLLGATE_FEED_VERSION=0.6.0_alpha4_pre17 \
python3 scripts/install-path-e2e.py --dry-run
```

The dry-run report, the raw JSON and the exact `pre16` evidence are in
[install-paths-dry-run-report.md](install-paths-dry-run-report.md).
