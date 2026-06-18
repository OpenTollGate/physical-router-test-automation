# Tag-Readiness Campaign — `main` @ `04ae54e` (target: v0.5.0)

Tracking checklist for the physical two-router tag-readiness assessment of
`OpenTollGate/tollgate-module-basic-go` `main` @ `04ae54e`.

This campaign is implemented as a **committed, reproducible** pytest + Makefile
suite in this repo (`physical-router-test-automation`), branch
`feat/tag-readiness-suite`. Nothing is committed to the module repo; the module
is only checked out in a throwaway detached worktree for static (`go test`)
analysis.

## Worktrees (isolation from other LLM sessions)

- [x] Module worktree (static tests only, **no commits**):
      `/home/c03rad0r/tollgate-worktrees/main-readiness` @ `04ae54e` (detached)
- [x] Harness worktree (all commits here):
      `/home/c03rad0r/physical-router-test-automation-wt` on `feat/tag-readiness-suite`

## Committed suite (authored → committed → run)

- [ ] `Makefile`: `tag-readiness-static` / `-preflight` / `-smoke` /
      `-two-router` / `-reboot` / `-postflight` / `-full` / `-report` +
      `TOLLGATE_MAIN_SRC` var
- [ ] `config/make-pytest-map.yaml`: register preflight/postflight targets
- [ ] `tests/scenarios/test_tag_readiness.py`: net-new preflight + postflight
- [ ] `docs/tag-readiness.md`: operator runbook
- [ ] `git commit` → `git push -u github feat/tag-readiness-suite`
- [ ] `gh pr create` on `OpenTollGate/physical-router-test-automation`

## Configure / reality (2026-06-18)

- [x] Password-auth: `c03rad0r123` works on both routers.
- [x] **beta = `192.168.244.1`** (OpenWrt 24.10.4, mediatek/filogic, TollGate present) — viable.
- [x] **`192.168.8.1` is NOT alpha** — stock GL-MT3000 on **OpenWrt 21.02**, no TollGate/nodogsplash. Cannot run `04ae54e` without a reflash (destructive; out of agreed scope). **alpha remains absent.**

## Deploy + run (lock held)

- [x] `make lock PHASE="v0.5.0 tag-readiness (04ae54e)"`
- [x] Deploy `04ae54e` to **beta** via **local build** (`deploy.sh` patched for
      stale LuCI paths + binary location; `--force-downgrade` for opkg). CI
      artifact path failed (Blossom/Nostr-only artifacts + 401 on rerun).
- [x] **Tier 0 — static:** build+vet clean; 12/14 modules PASS (root non-hermetic; `upstream_detector` go.mod not tidy).
- [x] **Tier 1 — single-router smoke (beta):** API smoke 27 passed / 24 skipped / 1 failed (env: `curl` not on image).
- [🚫] **Tier 2 — two-router e2e:** NOT EXECUTED — alpha on OpenWrt 21.02.
- [x] **Tier 3 — reboot-recovery (beta):** PASS (clean cycle, service auto-started).

## Report + issue

- [x] Write `docs/tag-readiness-reports/TEST-REPORT-main-04ae54e.md` (committed)
- [ ] `gh issue create --repo OpenTollGate/tollgate-module-basic-go @ 04ae54e`
      with verdict **READY-WITH-CAVEATS** + links to PR #38 and the report

## Cleanup

- [ ] `make unlock`
- [ ] `git worktree remove /home/c03rad0r/tollgate-worktrees/main-readiness`

## Known risks to document regardless of outcome

- beta (`192.168.244.1`) was unstable mid-probe (REACHABLE → DOWN)
- BoltDB degraded→full in-process upgrade is a known limitation; hotplug
  restart is the real recovery path on hardware
- PATs embedded in the module repo's git remotes (security)

---

# Phase 2 — Flash alpha + two-router e2e (2026-06-18, follow-up)

Correction from board probes: **both routers are GL-MT3000**. beta is
`glinet,gl-mt3000` on OpenWrt **24.10.4** (already modern). alpha is
`glinet,mt3000-snand` on OpenWrt **21.02** → needs the flash. Target module
build on both: **main @ `04ae54e`**.

Firmware image (GL-MT3000, 24.10.x sysupgrade, from releases.tollgate.me /
Blossom):
`https://blossom.primal.net/4c34ebf8b43790e07b40ad18d2a401bcea8ad9888105e7dcb48efc490795584c.bin`

Decisions: **plain `sysupgrade` (keep config)** on alpha (preserves
`192.168.8.1` + `c03rad0r123`); **leave beta as-is** (already 24.10.4).

## Checklist

- [ ] Download the blossom GL-MT3000 firmware image
- [ ] Flash alpha `192.168.8.1` via `sysupgrade` (keep config) → verify OpenWrt
      24.10.4 + still reachable at `192.168.8.1`
- [ ] alpha: `opkg remove tollgate-wrt` + install `04ae54e` ipk → verify version
- [ ] beta: verify still on OpenWrt 24.10.4 + `04ae54e`
- [ ] Update `routers.env` alpha host (`192.168.8.1`); install cashu CLI
      (`scripts/setup-cashu.sh`) so degraded/wallet tests run
- [ ] Establish alpha↔beta topology (WiFi association; else flag cable needed)
- [ ] `make lock`; preflight + Tier-1 smoke on **both** routers
- [ ] **Tier-2 two-router e2e** (`smoke-upstream`, `smoke-pin-upstream`,
      reseller, `payment-lifecycle`, `test_two_router*.py`)
- [ ] Tier-3 reboot-recovery (both) + postflight
- [ ] Update committed report + #169 with **pass/fail/skip × criticality ×
      tag-impact** matrix; commit + push
- [ ] Crosslink **#169 ↔ #154** (map results to #154's Hardware-Validation list)
- [ ] Cleanup: `make unlock`

## Risks (Phase 2)

- sysupgrade can still drop access if the firmware resets dropbear/LAN →
  U-Boot recovery via `scripts/uboot-recover.py` (GL-MT3000 supported).
- Tier 2 depends on alpha↔beta network proximity (WiFi or alpha-WAN→beta-LAN
  cable); separate USB-eth links to the host give them no direct path.

---

# Phase 3 — Resolve funding so router-to-router purchase can be verified (2026-06-18)

Root cause: `testnut.cashu.exchange` is a **healthy FakeWallet mint**, but the
funding **tool** is broken — `scripts/mint-token` pins `gonuts-tollgate v0.6.1`
(BOLT11 decode regression; fix is v0.7.1+ / `9b2b84344c3a`, not applied to
mint-token). The harness default `testnut-compat.mints.orangesync.tech` is also
TLS-down. So alpha's wallet stayed at 0 sats and the autopay purchase never ran.

Decisions: fund via the **host nutshell `cashu` CLI** (unblock now); give alpha
internet by connecting to **`EnterSSID-5GHz` (PSK `c03rad0r123`)** — alpha's own
upstream-WiFi path, cleaner than host NAT. Leave the `mint-token` gonuts bump as
a documented follow-up.

## Checklist

- [ ] Connect alpha → `EnterSSID-5GHz c03rad0r123`; verify `network_ok`/mint reachable
- [ ] Reconcile both routers `accepted_mints` → `testnut.cashu.exchange` (fixture `replace_mints`)
- [ ] Host nutshell `cashu`: fresh wallet, mint ~1100 sats at testnut, `send` → token
- [ ] Fund alpha (`tollgate wallet fund <token>`); verify balance > 0
- [ ] Switch alpha upstream → beta `TollGate-09E0`; verify autopay session + `network_ok` (**real purchase**)
- [ ] Re-run `TestTwoRouterFunded` + `test_two_router.py` via pytest
- [ ] Commit nutshell funding-path note to `docs/tag-readiness.md` (+ fixture); push
- [ ] Update report + #169 with funded two-router outcome
- [ ] Cleanup: teardown host changes, restore routers' upstream/config, `make unlock`
