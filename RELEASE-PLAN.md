# OpenTollGate Master Plan — Approve, Don't Merge

**Rule:** All merges require approval from the team (Amperstrand, Origami74, c03rad0r). We approve via GitHub PR reviews. We do not merge.

## Locked-In Decisions

1. No mocking — all tests are e2e on physical routers
2. `cli_command()` uses `tollgate --json` over SSH (socat eliminated)
3. All router HTTP calls use `wget` (no curl available, no opkg feeds)
4. Admin SPA at `http://<router>:8080/tollgate/admin.html`
5. Test mint: `https://testnut.cashu.exchange` (FakeWallet, auto-pays invoices)
6. Cashu venv at `$HOME/.cashu-venv` via `TOLLGATE_CASHU_VENV` env var
7. PR decomposition: #138→#139→#140→#141 (chain), #142 + #143 (independent)
8. PR #124 review fixes (binary, CIDR, deep copy) pushed to `develop`, reopened as PR #147

## Hardware

| Router | IP | Password |
|--------|-----|----------|
| Alpha | `10.47.41.1` | `c03rad0r123` |
| Beta | `192.168.244.1` | `c03rad0r123` |

## Current Review State

| PR | Title | c03rad0r | Amperstrand | Origami74 |
|----|-------|----------|-------------|-----------|
| #104 | Security & correctness fixes | APPROVED | — | — |
| #147 | Config schema, CLI --json (reopened #124) | — | — | — |
| #86 | Validate profit_share factors sum to 1.0 | APPROVED | — | — |
| #126 | V2 keyset ID support (CDK 0.16.0+) | APPROVED | — | — |
| #138 | PaymentMerchant interface | — | — | — |
| #139 | Mint health tracking, provider, sentinel | — | — | — |
| #140 | Degraded mode with dynamic upgrade/downgrade | — | — | — |
| #141 | Captive portal degraded-mode UI | — | — | — |
| #142 | SSL management rewrite in Go | — | — | — |
| #143 | CI build workflow, packaging | — | — | — |

## E2E Test Results (2026-05-28)

| Router | Passed | Failed | Skipped | XFailed | Time |
|--------|--------|--------|---------|---------|------|
| Alpha (10.47.41.1) | 95 | 0 | 163 | 3 | 542s |
| Beta (192.168.244.1) | 92 | 0 | 166 | 3 | 395s |

Test infrastructure: cashu 0.20.0 venv, tollgate --json CLI, wget + nc HTTP calls.

---

## Phase 1: Test Infrastructure

### 1A. Cashu venv setup

- [x] `python3 -m venv ~/.cashu-venv`
- [x] `~/.cashu-venv/bin/pip install --upgrade pip`
- [x] `~/.cashu-venv/bin/pip install cashu 'marshmallow<4'`
- [x] Patch pydantic bug: `sed -i 's/    active: bool$/    active: bool = True/' ~/.cashu-venv/lib/python3.*/site-packages/cashu/core/models.py`
- [x] Verify: `~/.cashu-venv/bin/cashu -h https://testnut.cashu.exchange -t balance` — expect `Balance: 0 sat`
- [x] Add `TOLLGATE_CASHU_VENV=$HOME/.cashu-venv` to `mint-health/routers.env`
- [x] Fix `scripts/setup-cashu.sh`: `python3.12`→`python3`, `sed -i ''`→`sed -i` (Linux)
- [x] Commit and push to `physical-router-test-automation`

### 1B. Full test suite on both routers

- [x] Alpha: 95 passed, 0 failed (542s)
- [x] Beta: 92 passed, 0 failed (395s)

---

## Phase 2: Pre-release Approvals & Validation (Issue #144)

### 2A. PR #104 — Security & correctness fixes

- [x] Post hardware test results as PR comment
- [x] Approve PR #104 (c03rad0r)
- [x] Add `ready for review` label
- [x] Request reviewer: Origami74 (Amperstrand is author)
- [x] Post review guide comment
- [ ] Wait for Amperstrand + Origami74 approval
- [ ] After all 3 approve: validate happy path on hardware

### 2B. PR #147 — Config schema, CLI --json (reopened #124)

- [x] Post hardware test results + `--json` validation as PR comment
- [ ] Approve PR #147 (c03rad0r — cannot approve own PR)
- [x] Add `upcoming-release-lets-prioritize` + `ready for review` labels
- [x] Request reviewers: Amperstrand + Origami74
- [x] Post review guide comment
- [ ] Wait for Amperstrand + Origami74 approval
- [ ] After all 3 approve: validate on hardware:
  - [ ] `tollgate --json config schema` returns valid schema (67 entries)
  - [ ] `tollgate --json config get` returns full config JSON
  - [ ] `tollgate --json config set <key> <value>` persists to disk
  - [ ] Captive portal happy path works on both routers
  - [ ] Full pytest suite passes

### 2C. Release tag

- [ ] All #144 validations pass
- [ ] Tag release on `main`
- [ ] Blocked: all 3 team approvals on #104 + #147

---

## Phase 3: Frontend Delivery for Endo (Issue #145)

Depends on: Phase 2 (#147 on `main`)

### 3A. TollGate admin SPA

- [ ] Post test results on portal PR #10 and PR #11
- [ ] Approve portal PR #10 (c03rad0r)
- [ ] Approve portal PR #11 (c03rad0r)
- [ ] Wait for Amperstrand + Origami74 approvals
- [ ] After merge: validate on hardware:
  - [ ] Schema match: `tollgate --json config schema` vs admin SPA form fields
  - [ ] Admin login → dashboard → health/version/wallet
  - [ ] Settings page → schema-driven form → save → persists to disk
  - [ ] Captive portal → payment → gate opens
  - [ ] ubus `config_schema`, `config_get`, `config_set`, `wallet_balance` respond
- [ ] Run Playwright admin SPA tests on both routers

### 3B. net4sats configurationwizzard

- [ ] Validate on hardware (Alpha + Beta):
  - [ ] Captive portal → payment → gate opens with configurationwizzard theme
  - [ ] Configurationwizzard reads and renders `config_schema`
- [ ] Post results on https://github.com/net4sats/configurationwizzard/issues/3

### 3C. Endo deployment

- [ ] Deploy validated frontends to Endo's test router
- [ ] Blocked: physical delivery

---

## Phase 4: Post-release PR Backlog (Issue #146)

### 4A. Tier 1 — No dependencies

| PR | c03rad0r | Amperstrand | Origami74 |
|----|----------|-------------|-----------|
| #86 (profit_share) | APPROVED | needs review | needs review |
| #126 (V2 keyset ID) | APPROVED | needs review | needs review |
| #138 (merchant_types) | needs review | needs review | needs review |
| #143 (CI infra) | needs review | needs review | needs review |

- [x] Post hardware validation results on #86
- [x] Post hardware validation results on #126
- [x] Add `ready for review` labels to #86, #126, #138-#143
- [x] Request reviewers on #86, #126, #138-#143
- [ ] Approve #138 (c03rad0r — cannot approve own PR)
- [ ] Approve #143 (c03rad0r — cannot approve own PR)
- [ ] Wait for Amperstrand + Origami74 on all 4

### 4B. Tier 2 — Sequential chain

```
#138 → #139 → #140 → #141
#142 — independent
```

- [ ] Check #139 mergeability after Tier 1, rebase if needed
- [ ] Approve #139 (c03rad0r), post hardware results
- [ ] Wait for Amperstrand + Origami74
- [ ] Check #140 after #139, rebase if needed
- [ ] Approve #140 (c03rad0r), post hardware results
- [ ] Wait for Amperstrand + Origami74
- [ ] Check #141 after #140, rebase if needed
- [ ] Approve #141 (c03rad0r), post hardware results
- [ ] Wait for Amperstrand + Origami74
- [ ] Check #142 mergeability, rebase if needed
- [ ] Approve #142 (c03rad0r), post hardware results
- [ ] Wait for Amperstrand + Origami74

### 4C. Tier 3 — Lower priority

- [ ] Rebase #106 (session eviction) after Tier 1
- [ ] Approve #106 after rebase
- [ ] Review #125 (OpenWrt feed — DRAFT, 120 files)

---

## Phase 5: Test Automation Maintenance

- [x] Fix `scripts/setup-cashu.sh` Linux compat (Phase 1A)
- [x] Add `TOLLGATE_CASHU_VENV` to `mint-health/routers.env`
- [x] Fix `post_payment_event` — curl → nc (busybox wget discards body on errors)
- [x] Fix `test_keyset_id_versions` — cashu 0.20.0 token structure
- [x] Fix `test_mint_502_handling` — wget connection error strings
- [x] Fix `skip_if_no_luci` — curl → wget --spider
- [ ] Update `REMAINING-WORK-PLAN.md`
- [ ] nsite dashboard — deferred (Blossom unreachable)

---

## Phase 6: CI Fix & Lightning Invoice Investigation

### 6A. Fix PR #143 CI — revert artifact action downgrade

- [ ] In `/tmp/pr118-worktrees/pr-i-ci-infra/`, edit `.github/workflows/build-package.yml`
- [ ] 3x `upload-artifact@v3` → `@v7`, 3x `download-artifact@v3` → `@v8`
- [ ] Commit and force-push to `pr-i-ci-infra` via `github` remote
- [ ] Verify CI passes

### 6B. Draft PR for v3 downgrade (act compatibility)

- [ ] Create branch `fix/artifact-actions-v3-act-compat` from `main`
- [ ] Apply 6x v3 downgrades
- [ ] Open as DRAFT PR with body explaining act compatibility
- [ ] Create issue: "act local CI requires artifact actions v3, but GitHub auto-fails v3"

### 6C. Issue #149 — Lightning invoice "amount 0 sats" reproduction

- [ ] Write `tests/api/test_lightning_invoice.py` with setup/teardown
- [ ] Test cases:
  - [ ] POST `{ amount: 0, mint_url }` → expect "amount must be greater than zero"
  - [ ] POST `{ amount: 4, device }` (missing mint_url) → expect "amount and mint_url are required"
  - [ ] POST `{ amount: 4, mint_url }` → expect success (quote + invoice)
  - [ ] POST `{ amount: "", mint_url }` (string amount) → verify behavior
- [ ] Run on Alpha + Beta
- [ ] Post root cause analysis + results on issue #149

---

## Execution Order

```
1. Update RELEASE-PLAN.md with new phases                          ← now
2. Fix PR #143 CI (revert v3→v7/v8, force-push)
3. Create draft PR for v3 downgrade + issue
4. Write test_lightning_invoice.py, run on both routers
5. Create 'ready for review' label, apply to all PRs
6. Request formal reviewers on all PRs
7. Post review guides on #104 and #147
8. Commit + push updated plan
9. Wait for Amperstrand + Origami74 reviews
10. Hardware validation after merges
11. Frontend validation (Phase 3)
12. Tier 2 approvals (Phase 4B)
```
