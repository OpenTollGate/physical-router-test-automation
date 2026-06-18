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
