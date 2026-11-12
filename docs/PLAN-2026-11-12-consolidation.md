# Consolidation plan — fleet handoff and gateway mirror

Status: **gateway promoted; automation PR awaiting CI/merge** (2026-11-12)
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
- [x] Re-check both topology predicates immediately before each merge.

### 2. Documentation

- [x] Record conwrt PR #567 as merged upstream in both handoff documents.
- [x] Record the final promoted gateway SHA in the fleet handoff (`869eeea31a227062c7eb60f47fd2d598e6b587c9`).
- [x] Record only proven x280 reachability: do not claim the fleet was verified while x280 is unreachable.
- [ ] Update this checklist with test evidence and merge SHAs.

### 3. Gateway test gate

- [x] Restore x280, or establish an isolated, hardware-free CI lane for the exact candidate SHA.
  Used GitHub-hosted CI, run `43304370700`; x280 remains unavailable.
- [x] Run the affected Python gateway suites, including tool-budget tests added to the canonical GitHub default: **209 passed, 1 skipped**.
- [x] Run the declared tool-budget Node tests: `node-tool-budget` **SUCCESS** in run `43304370700`.
- [x] Classify broader project lane: **NOT RUN** — `make test-all` includes live `localhost:7779`/`:9099` service prerequisites and provider-backed tools; not a hardware-free docs gate.
- [x] Check candidate diff and private-key additions. Eight pre-existing formatting exceptions (Markdown metadata hard breaks and test EOF blank lines) were emitted and allowlisted individually; no new private-key additions.
- [x] Record exact-head CI evidence: https://github.com/c03rad0r/hermes-orchestration/actions/runs/43304370700 (**SUCCESS** at source SHA `869eeea31a227062c7eb60f47fd2d598e6b587c9`).

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

- [x] Confirm the candidate equals `github/master` and fast-forwards the still-current lab bare.
- [x] Push the tested commit to the lab bare `master`: `da5930db..869eeea31`, no force.
- [x] Read back lab bare `rev-parse master`: `869eeea31a227062c7eb60f47fd2d598e6b587c9`.
- [x] Verify `27a8303b4` (COMFAST reservation) remains an ancestor.
- [x] Synchronize `worktrees/mt6000-default` to `869eeea31` by fast-forward; unrelated `gateway-iac` untouched.

### 5. Automation PR

- [x] Push `pr/fleet-consolidation-20261112` and open automation PR https://github.com/OpenTollGate/physical-router-test-automation/pull/345.
- [x] Verify the diff contains only the intended handoff/plan changes and no credentials or unrelated files.
- [x] Hardware-free CI on isolated GitHub-hosted runner: https://github.com/c03rad0r/hermes-orchestration/actions/runs/43317664973 — **SUCCESS** at `05c507d`: pytest collection, **1250 passed, 5 skipped, 3 xfailed**, Ruff, mypy, story coverage, and gitleaks (no leaks).
- [ ] Validate the exact final documentation-only head, then merge.
- [ ] Merge with the repository's normal merge method; never force-push `main`.
- [ ] Verify the resulting `origin/main` contains the documentation and plan.

### 6. Final reconciliation

- [ ] Synchronize relevant local default worktrees to their merged tips.
- [ ] Verify no candidate commits remain unpushed or unmerged.
- [ ] Once x280 is available, update its automation checkout and verify bench services/lease, without claiming that was done if the host remains down.
- [ ] Record any remaining operational item separately from code consolidation.

## Stop conditions

Stop before a merge when a fast-forward relationship is lost, an unrelated modification appears, a test fails or cannot run on an approved host, the lab bare advances before promotion, or secrets/unrelated files enter the diff.

## Execution evidence — gateway

| Gate | Result |
|---|---|
| Exact source | `869eeea31a227062c7eb60f47fd2d598e6b587c9` |
| CI | https://github.com/c03rad0r/hermes-orchestration/actions/runs/43304370700 |
| Python | 209 passed, 1 skipped |
| Node tool-budget | SUCCESS |
| Broader live-service lane | NOT RUN — see explanation above |
| Lab mirror before/after | `da5930db..869eeea31` |
| Mirror read-back | `869eeea31a227062c7eb60f47fd2d598e6b587c9` |
| Local default checkout | `mt6000-default` fast-forwarded to `869eeea31` |

The disposable validator PR is https://github.com/c03rad0r/hermes-orchestration/pull/3.
It is test infrastructure, not a gateway source change, and is to be closed
without merging after recording the final external automation run.

## Fixture repairs required by the first automation unit run

The first full offline unit run also uncovered three pre-existing test issues,
not new router behavior. The PR includes their narrowly scoped corrections:

- the credential test now asserts env-only injection rather than reading a
  committed password that was deliberately removed;
- the read-only mock returns a curl HTTP status so it does not accidentally
  require the newer wget fallback;
- the lock tests explicitly simulate absence of `tollgate_lab` instead of
  testing that installed package's different lock implementation.

Gitleaks also identified three pre-existing public no-value fixtures elsewhere
in the repository. The allowlist covers exactly those unchanged public values,
with the safety rationale in `.gitleaks.toml`; no new credential allowlisting or
production behavior change is included. The full offline suite then passed with
no failures or errors, and gitleaks reported no leaks.
