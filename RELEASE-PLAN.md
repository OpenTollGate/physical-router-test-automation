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
| #104 | Security & correctness fixes | — | — | — |
| #147 | Config schema, CLI --json (reopened #124) | — | — | — |
| #86 | Validate profit_share factors sum to 1.0 | APPROVED | — | — |
| #126 | V2 keyset ID support (CDK 0.16.0+) | APPROVED | — | — |
| #138 | PaymentMerchant interface | — | — | — |
| #139 | Mint health tracking, provider, sentinel | — | — | — |
| #140 | Degraded mode with dynamic upgrade/downgrade | — | — | — |
| #141 | Captive portal degraded-mode UI | — | — | — |
| #142 | SSL management rewrite in Go | — | — | — |
| #143 | CI build workflow, packaging | — | — | — |

---

## Phase 1: Test Infrastructure

### 1A. Cashu venv setup

- [ ] `python3 -m venv ~/.cashu-venv`
- [ ] `~/.cashu-venv/bin/pip install --upgrade pip`
- [ ] `~/.cashu-venv/bin/pip install cashu 'marshmallow<4'`
- [ ] Patch pydantic bug: `sed -i 's/    active: bool$/    active: bool = True/' ~/.cashu-venv/lib/python3.*/site-packages/cashu/core/models.py`
- [ ] Verify: `~/.cashu-venv/bin/cashu -h https://testnut.cashu.exchange -t balance` — expect `Balance: 0 sat`
- [ ] Add `TOLLGATE_CASHU_VENV=$HOME/.cashu-venv` to `mint-health/routers.env`
- [ ] Fix `scripts/setup-cashu.sh`: `python3.12`→`python3`, `sed -i ''`→`sed -i` (Linux)
- [ ] Commit and push to `physical-router-test-automation`

### 1B. Full test suite on both routers

- [ ] Alpha: `TOLLGATE_SSH_HOST=10.47.41.1 ROUTER_PASSWORD=c03rad0r123 TOLLGATE_CASHU_VENV=$HOME/.cashu-venv pytest tests/api/ -m "api and not virtual_lab and not rust_only" --timeout=120 --tb=short -q`
- [ ] Beta: `TOLLGATE_SSH_HOST=192.168.244.1 ROUTER_PASSWORD=c03rad0r123 TOLLGATE_CASHU_VENV=$HOME/.cashu-venv pytest tests/api/ -m "api and not virtual_lab and not rust_only" --timeout=120 --tb=short -q`
- [ ] Expected: ~93 pass / 0 fail on both (up from 87/7 and 80/6)

---

## Phase 2: Pre-release Approvals & Validation (Issue #144)

### 2A. PR #104 — Security & correctness fixes

- [ ] Post hardware test results as PR comment
- [ ] Approve PR #104 (c03rad0r)
- [ ] Wait for Amperstrand approval
- [ ] Wait for Origami74 approval
- [ ] After all 3 approve: validate happy path on hardware

### 2B. PR #147 — Config schema, CLI --json (reopened #124)

- [ ] Post hardware test results + `--json` validation as PR comment
- [ ] Approve PR #147 (c03rad0r)
- [ ] Wait for Amperstrand approval
- [ ] Wait for Origami74 approval
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

- [ ] Post hardware validation results on #86
- [ ] Post hardware validation results on #126
- [ ] Approve #138 (c03rad0r)
- [ ] Approve #143 (c03rad0r)
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

- [ ] Fix `scripts/setup-cashu.sh` Linux compat (Phase 1A)
- [ ] Add `TOLLGATE_CASHU_VENV` to `mint-health/routers.env`
- [ ] Update `REMAINING-WORK-PLAN.md`
- [ ] nsite dashboard — deferred (Blossom unreachable)

---

## Execution Order

```
1. Cashu venv setup + script fix + commit
2. Full test suite on Alpha + Beta
3. Post results on PR #104, #147, #86, #126
4. Approve all PRs (c03rad0r)
5. Wait for Amperstrand + Origami74 reviews
6. Hardware validation after merges
7. Frontend validation (Phase 3)
8. Tier 2 approvals (Phase 4B)
```
