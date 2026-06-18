# Tag-Readiness Campaign

Reproducible physical two-router release-gate for
[`tollgate-module-basic-go`](https://github.com/OpenTollgate/tollgate-module-basic-go)
`main`. Implemented as committed Makefile targets + a pytest module so every
check is version-controlled — no ad-hoc shell one-liners.

## What it tests

A pinned module commit is assessed in four tiers across two physical routers
(alpha = primary, beta = secondary):

| Tier | Target | What | Router |
|------|--------|------|--------|
| 0 — static | `tag-readiness-static` | `go build`/`vet`/`test ./...` in the module worktree | none |
| 1 — smoke | `tag-readiness-smoke` | degraded lifecycle, hostname, SSL status | alpha |
| 2 — two-router | `tag-readiness-two-router` | upstream renewal, pin-upstream, cashu payment e2e | alpha + beta |
| 3 — reboot | `tag-readiness-reboot` | reboot-recovery (no firmware sysupgrade) | alpha |

Plus net-new checks in `tests/scenarios/test_tag_readiness.py`:

- **`tag-readiness-preflight`** — both routers SSH-reachable, deployed version
  captured, TollGate SSIDs broadcast, no dual-WWAN pitfall, no crash loop
  (run *before* anything mutates state, with `--no-deploy`).
- **`tag-readiness-postflight`** — service still alive on both, no new
  panic/fatal log lines, no leftover mint blocks (iptables / `/etc/hosts`),
  wallet balance still answers.

## Prerequisites

1. **Module worktree** (static tier; throwaway, nothing committed here):
   ```sh
   git -C ~/tollgate-module-basic-go fetch github
   git -C ~/tollgate-module-basic-go worktree add --detach \
       ~/tollgate-worktrees/main-readiness <commit-sha>
   ```
2. **Router inventory** (`mint-health/routers.env`, gitignored) — set the
   primary (`alpha`) and secondary (`beta`) hosts, e.g.:
   ```sh
   ROUTER_ALPHA_HOST=192.168.8.1
   ROUTER_ALPHA_LAN_HOST=192.168.8.1
   ROUTER_BETA_HOST=192.168.244.1
   ROUTER_BETA_LAN_HOST=192.168.244.1
   ROUTER_ALPHA_PASSWORD=c03rad0r123   # optional; else export TOLLGATE_LUCI_PASSWORD
   ROUTER_BETA_PASSWORD=c03rad0r123
   ```
3. **Deploy** the build under test to both routers, e.g. the CI `.ipk`:
   ```sh
   export TOLLGATE_LUCI_PASSWORD=c03rad0r123
   ./scripts/deploy-ci.sh main <run-id> 192.168.8.1
   ./scripts/deploy-ci.sh main <run-id> 192.168.244.1
   ```

## Running

```sh
cd ~/physical-router-test-automation-wt          # the feat/tag-readiness-suite worktree
make lock PHASE="v0.5.0 tag-readiness (<sha>)"

make tag-readiness-static                         # Tier 0 (no router)
make tag-readiness-preflight ROUTER=alpha         # pristine checks, both routers
make tag-readiness-smoke      ROUTER=alpha         # Tier 1
make tag-readiness-two-router ROUTER=alpha         # Tier 2
make tag-readiness-reboot                          # Tier 3
make tag-readiness-postflight ROUTER=alpha         # steady-state checks, both routers
make tag-readiness-report                          # collect + render results run-dir

# or all at once:
make tag-readiness-full ROUTER=alpha

make unlock
```

`tag-readiness-full` stops on the first failing tier (reboot is allowed to fail
non-fatally with `-`). Each router-touching target enforces the hardware lock.

## Output

- Per-tier pytest/Playwright JUnit + HTML under `results/<run_id>/` (gitignored).
- Rendered `results/<run_id>/report/index.html` via `tag-readiness-report`.
- The operator writes a verdict report to
  `docs/tag-readiness-reports/TEST-REPORT-main-<sha>.md` (committed) and opens a
  tag-readiness issue on the module repo pointing at the commit.

## Notes / known limits

- Preflight/postflight intentionally run with `--no-deploy` so the session
  fixture does not rewrite mint config before/after the observations.
- Two-router tiers require beta reachable; if it is offline, preflight fails
  fast (correct readiness signal).
- BoltDB degraded→full in-process upgrade is a documented limitation; the
  hotplug restart is the real recovery path on hardware.
