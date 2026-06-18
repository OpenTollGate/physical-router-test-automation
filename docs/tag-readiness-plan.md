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

## Configure (gitignored, uncommitted)

- [ ] Password-auth to identify `192.168.8.1` (expect alpha / `TollGate-EVXZ`)
      and `192.168.244.1` (expect beta / `TollGate-24A6`); re-probe beta
- [ ] Set `ROUTER_ALPHA_HOST/_LAN_HOST=192.168.8.1` in
      `mint-health/routers.env` + `upstream-wifi/routers.env`
- [ ] `export TOLLGATE_LUCI_PASSWORD=c03rad0r123`

## Deploy + run (each tier gated on router health; lock held throughout)

- [ ] `make lock PHASE="v0.5.0 tag-readiness (04ae54e)"`
- [ ] Deploy CI artifact (`.ipk` built for `04ae54e`) to both routers
- [ ] **Tier 0 — static:** `make tag-readiness-static` (go build/vet/test)
- [ ] **Tier 1 — single-router smoke (alpha):** `make tag-readiness-smoke`
- [ ] **Tier 2 — two-router e2e (alpha+beta):** `make tag-readiness-two-router`
- [ ] **Tier 3 — reboot-recovery:** `make tag-readiness-reboot`

## Report + issue

- [ ] `make tag-readiness-report` (render run-dir)
- [ ] Write `docs/tag-readiness-reports/TEST-REPORT-main-04ae54e.md` (committed)
- [ ] `gh issue create --repo OpenTollGate/tollgate-module-basic-go @ 04ae54e`
      with verdict (READY / NOT-READY / READY-WITH-CAVEATS) + per-tier table +
      links to this PR and the committed report

## Cleanup

- [ ] `make unlock`
- [ ] `git worktree remove /home/c03rad0r/tollgate-worktrees/main-readiness`
      (keep harness worktree until PR merges)

## Known risks to document regardless of outcome

- beta (`192.168.244.1`) was unstable mid-probe (REACHABLE → DOWN)
- BoltDB degraded→full in-process upgrade is a known limitation; hotplug
  restart is the real recovery path on hardware
- PATs embedded in the module repo's git remotes (security)
