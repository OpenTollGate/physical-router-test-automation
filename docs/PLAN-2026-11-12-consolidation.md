# Consolidation plan — fleet handoff and gateway mirror

Status: **in progress** (started 2026-11-12)  
Automation branch: `pr/fleet-consolidation-20261112`  
Gateway validation branch: `pr/gateway-mirror-validation-20261112`

## Scope and invariants

- Bring the fleet handoff onto current `OpenTollGate/physical-router-test-automation:main`.
- Test `c03rad0r/hermes-orchestration:master` at `869eeea31` and promote that exact commit to the lab `upstream/master` mirror by **fast-forward/merge only**.
- Do not rewrite published history, force-push, or duplicate the already-merged commits.
- Do not touch unrelated worktrees, especially the dirty `gateway-iac`.
- Run no tests on T14. Use x280 or isolated CI; no router configuration is in scope.
- Never treat the production MT6000 as a test target. Read-only production verification is optional, not part of deployment.
- conwrt PR [#567](https://github.com/Amperstrand/conwrt/pull/567) is already merged as `ab442ffc` (2026-10-28); it requires no rebase.

## Initial topology verified

```text
Automation local main        6e1208f -> origin/main ab0c4b6 (fast-forwarded)
Automation integration        ab0c4b6
Gateway local default         774b6fd72
Gateway GitHub candidate      869eeea31
Gateway lab bare              da5930db (ancestor of the candidate)
Candidate vs lab bare         12 ahead, 0 behind: fast-forward, not a rebase
Conwrt                        MERGED ab442ffc
x280                          unreachable from T14 and cobradorwave routes
```

The gateway candidate includes the nine commits already pushed to GitHub plus three newer tool-budget commits. It therefore cannot be promoted with only the older local working-tree tests: the complete candidate must be tested.

## Checklist

### 1. State and integration

- [x] Fetch the automation default branch and fast-forward the clean local `main`.
- [x] Create the isolated automation integration branch.
- [x] Fetch gateway remotes and verify `upstream/master` is an ancestor of `github/master`.
- [x] Create an isolated gateway worktree at the exact GitHub candidate.
- [ ] Re-check both topology predicates immediately before each merge.

### 2. Documentation

- [x] Record conwrt PR #567 as merged upstream in both handoff documents.
- [ ] Record the final promoted gateway SHA in the fleet handoff.
- [x] Record only proven x280 reachability: do not claim the fleet was verified while x280 is unreachable.
- [ ] Update this checklist with test evidence and merge SHAs.

### 3. Gateway test gate

- [ ] Restore x280, or establish an isolated, hardware-free CI lane for the exact candidate SHA.
- [ ] Run the affected Python gateway suites, including tool-budget tests added to the canonical GitHub default.
- [ ] Run the declared tool-budget Node tests.
- [ ] Run the broader hardware-safe project test target if it does not require live credentials or production changes.
- [ ] Check `git diff --check upstream/master...candidate` and secret hygiene.
- [ ] Record exact-head CI/test evidence. Do not merge if tests cannot run or fail.

Targeted Python files:

```text
tests/test_mt6000_gateway_contract.py
tests/test_e750_recovery_contract.py
tests/fleet/test_x280_netplan.py
tests/test_remote_divergence_check.py
tests/test_model_tiers_spatial_intent.py
tests/test_hermes_tool_budget_real_probe.py
tests/test_hermes_tool_budget_trial.py
scripts/engine/test_lane_wiring_audit.py
scripts/engine/test_latency_telemetry.py
scripts/engine/test_manager_tier_deepseek_stopgap.py
scripts/engine/test_model_quality_cost.py
scripts/engine/test_numes_lane.py
scripts/engine/test_review_findings.py
```

Node tests:

```text
state/opencode/plugins/hermes-tool-budget/catalog.test.ts
state/opencode/plugins/hermes-tool-budget/layout.test.ts
```

### 4. Gateway promotion

- [ ] Confirm the candidate equals `github/master` and fast-forwards the still-current lab bare.
- [ ] Push the tested commit to the lab bare `master`, never forced.
- [ ] Verify the lab bare equals that exact tested commit.
- [ ] Verify `27a8303b4` (COMFAST reservation) remains an ancestor.
- [ ] Synchronize the local gateway default checkout without touching unrelated worktrees.

### 5. Automation PR

- [ ] Push `pr/fleet-consolidation-20261112` and open a PR against current `main`.
- [ ] Verify the diff contains only the intended handoff/plan changes and no credentials or unrelated files.
- [ ] Require existing hardware-free GitHub CI to pass at the PR head.
- [ ] Merge with the repository's normal merge method; never force-push `main`.
- [ ] Verify the resulting `origin/main` contains the documentation and plan.

### 6. Final reconciliation

- [ ] Synchronize relevant local default worktrees to their merged tips.
- [ ] Verify no candidate commits remain unpushed or unmerged.
- [ ] Once x280 is available, update its automation checkout and verify bench services/lease, without claiming that was done if the host remains down.
- [ ] Record any remaining operational item separately from code consolidation.

## Stop conditions

Stop before a merge when a fast-forward relationship is lost, an unrelated modification appears, a test fails or cannot run on an approved host, the lab bare advances before promotion, or secrets/unrelated files enter the diff.
