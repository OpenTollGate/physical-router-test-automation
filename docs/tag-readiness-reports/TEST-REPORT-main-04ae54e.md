# Tag-Readiness Report — `main @ 04ae54e`

**Repo:** `OpenTollGate/tollgate-module-basic-go`
**Commit:** `04ae54e77aebc2dab6bf9e6f58c6cb8dd6ce22a2` *(ci: skip build/publish pipeline for fork PRs (#166))*
**Date:** 2026-06-18
**Suite:** [`feat/tag-readiness-suite`](https://github.com/OpenTollGate/physical-router-test-automation/pull/38) in `physical-router-test-automation`
**Latest tag:** `v0.5.0-alpha2` (this commit is post-alpha2, heading toward v0.5.0)

## Verdict: ⚠️ READY-WITH-CAVEATS

- **Acceptable for a `v0.5.0-alpha3` / `-beta1` pre-release tag.**
- **NOT ready for a stable `v0.5.0` tag** until the caveats below are resolved.

The commit builds cleanly and is healthy on real `mediatek/filogic` hardware with
core API + reboot-recovery passing. However the **headline v0.5.0 features
(two-router upstream autopay / reseller, and degraded-mode on hardware) were not
exercised** because the second lab router is on an unsupported OpenWrt release.

## Environment

| Role | Identity | OpenWrt | Arch | Notes |
|------|----------|---------|------|-------|
| beta (primary) | `192.168.244.1` | 24.10.4 | mediatek/filogic (aarch64) | Deployed `04ae54e` via local-build `.ipk` (`--force-downgrade`). Healthy. |
| alpha (secondary) | `192.168.8.1` | **21.02-SNAPSHOT** (stock GL-MT3000) | mediatek/mt7981 | **No TollGate, no nodogsplash.** Cannot run the `04ae54e` package without a reflash. |

Build host: Go 1.26.0 linux/amd64. Hardware lock held throughout (`make lock`).

## Results by tier

### Tier 0 — static (worktree @ `04ae54e`, no router)

| Check | Result |
|-------|--------|
| `go build ./...` (root module) | ✅ PASS |
| `go vet ./...` (root module) | ✅ PASS |
| `go test` per standalone module | ✅ 12 / 14 PASS |

- ❌ **Root module** (`src/`, `package main`): test fatals — `open /etc/tollgate/config.json: no such file or directory`. **Non-hermetic off-router** (depends on host having `/etc/tollgate/`); not a regression — CI runs it in a provisioned environment.
- ⚠️ **`upstream_detector`**: `go test` blocked by `go: updates to go.mod needed; to update it: go mod tidy`. Not a logic failure, but **go.mod is not tidy** on `main`.

### Tier 1 — single-router API smoke (beta @ `04ae54e`)

`pytest tests/api -m smoke --no-deploy` → **27 passed, 24 skipped, 1 failed** (7.9s)

- ✅ Health (`/`, `/pay`, `/whoami`, `/balance`), info endpoint (discovery event, metric, step_size, price_per_step, tips), hostname (set + persists across restart), CLI version (fields, openwrt, hex commit).
- ⏭️ 24 skipped: tests needing the cashu CLI / wallet funding / phone (cashu venv not installed on the test host).
- ❌ 1 failed: `tests/api/test_portal_verify.py::test_tollgate_backend_healthy` — **environmental**, not a regression: `ash: curl: not found` on beta's minimal image. The backend itself is healthy (`tollgate status` → `running/network_ok: true`; `test_root_endpoint` passed).

### Tier 2 — two-router e2e (alpha + beta)

**🚫 NOT EXECUTED — blocked by hardware.** The only candidate "alpha" router
(`192.168.8.1`) is a stock GL-MT3000 on **OpenWrt 21.02** with no TollGate /
nodogsplash. The `04ae54e` package targets OpenWrt ≤24.10 (ipk) / 25.x (apk);
21.02 is far too old. Making it alpha requires a **reflash** to OpenWrt 24.10+,
which is destructive and outside the agreed (non-firmware) scope.

Unverified v0.5.0 hardware coverage as a result:
- `smoke-upstream` (offline renewal via LAN) — `test_two_router.py`
- `smoke-pin-upstream`, reseller mode
- `smoke-degraded` (degraded merchant lifecycle on hardware) — *also skipped:
  cashu CLI not installed locally*

### Tier 3 — reboot-recovery (beta @ `04ae54e`)

✅ **PASS** — clean cycle: `reboot` → offline → service auto-started via procd
within ~20s; fresh boot confirmed (`uptime=32s`); build persisted (`04ae54e77ae`).
No firmware sysupgrade (per scope).

## Findings & risks

1. **Two-router HW e2e unverified** (headline v0.5.0 upstream/reseller features). alpha must be reflashed to OpenWrt 24.10+ to run the suite.
2. **Degraded-mode-on-hardware unverified** — `test_mint_health` skipped (local cashu CLI missing).
3. **Root-module test is non-hermetic** — fails off-router (`/etc/tollgate/config.json`). Recommend a test fixture that writes a temp config dir so `go test ./...` passes on a clean machine.
4. **`upstream_detector/go.mod` not tidy** — `go mod tidy` needed on `main`.
5. **Process / tooling (not code regressions):**
   - CI build artifacts are published to Blossom/Nostr mirrors (CHANGELOG #155), **not GitHub Actions artifacts** — `deploy-ci.sh` / `deploy_branch` cannot fetch them; the rerun path hit `HTTP 401`. A local-build deploy (`deploy.sh`) was required.
   - Harness `scripts/deploy.sh` has **stale LuCI paths** (`luci-app-tollgate-payments.*`) removed at `04ae54e`, and assumes binaries at repo-root `bin/` (they now build to `src/bin/`). Both block the local-build deploy and required patching. (Filed as part of PR #38 context; fix belongs in the test-automation repo.)
   - `deploy.sh` packages a non-semver version (`04ae54e77ae`) which opkg treats as a *downgrade* from `v0.4.0` → needs `--force-downgrade`.

## Recommendation

For **`v0.5.0-alpha3` / `-beta1`**: safe to tag — build is clean, core API +
reboot-recovery green on real hardware.

For **stable `v0.5.0`**: first
1. Reflash alpha to OpenWrt 24.10+ and run the two-router tier (`smoke-upstream`, reseller) + `smoke-degraded` on hardware.
2. Install the cashu CLI on the test host so the degraded-mode / wallet tests run instead of skipping.
3. On `main`: `go mod tidy` in `upstream_detector`; make the root-module test hermetic (temp config dir).
4. In the test-automation repo: update `deploy.sh` (binary paths + drop dead LuCI files) and teach `deploy_branch` to fetch from Blossom/Nostr (or accept a local `.ipk` path).

## Reproduction

```sh
# in the physical-router-test-automation worktree (feat/tag-readiness-suite)
make lock PHASE="v0.5.0 tag-readiness (04ae54e)"
# static (no router):
make tag-readiness-static
# deploy 04ae54e to beta (local build, force-downgrade):
./scripts/deploy.sh 04ae54e 192.168.244.1 root aarch64_cortex-a53   # see PR #38 notes for required patches
# single-router API smoke + reboot:
TOLLGATE_SSH_HOST=192.168.244.1 TOLLGATE_SSH_PASSWORD=c03rad0r123 \
  pytest tests/api -m smoke --no-deploy
make unlock
```

Tier 2 requires alpha on OpenWrt ≥24.10 (currently 21.02).
