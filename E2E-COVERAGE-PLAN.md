# E2E Test Coverage Plan — PR #124, PR #11, configurationwizzard

## Infrastructure

| Resource | Details |
|---|---|
| Workstation | CobradorWave (`100.90.101.9`) — direct SSH to routers |
| Router Alpha | `10.47.41.1` (GL.iNet, ARM64) |
| Router Beta | `192.168.244.1` (GL.iNet, ARM64) |
| Test repo | `/home/c03rad0r/physical-router-test-automation` |

## Three Components Under Test

1. **PR #124** — `tollgate-module-basic-go` `develop` branch (config schema, `--json` CLI, build-tag fix)
2. **PR #11** — `tollgate-captive-portal-site` (admin SPA + rpcd plugin + packaging)
3. **configurationwizzard** — `net4sats/configurationwizzard` `captive-portal` branch (portal SPA)

## Already Implemented

| Component | Test File | Tests | Framework |
|---|---|---|---|
| PR #124 CLI backend | `scripts/test-configwizzard-e2e.sh` Phase 1 | 13 | Bash |
| PR #124 config save | `scripts/test-config-save-e2e.sh` | 7 | Bash |
| PR #124 CLI (pytest) | `tests/api/test_cli_version.py` | 5 | pytest |
| PR #124 CLI (pytest) | `tests/api/test_cli_wallet.py` | 5 | pytest |
| PR #124 CLI (pytest) | `tests/api/test_luci_admin_ui.py` | 10 | pytest |
| PR #11 rpcd plugin | `scripts/test-configwizzard-e2e.sh` Phase 2 | 10 | Bash |
| PR #11 SPA files | `scripts/test-configwizzard-e2e.sh` Phase 4 | 4 | Bash |
| Integration (rpcd ↔ CLI) | `scripts/test-configwizzard-e2e.sh` Phase 5 | 3 | Bash |
| :2121 API | `scripts/test-configwizzard-e2e.sh` Phase 3 | 3 | Bash |
| :2121 API (pytest) | `tests/api/test_*.py` (49 files) | ~230 | pytest |
| Captive portal | `tests/browser/captive_portal.spec.mjs` | 6 | Playwright |
| Degraded portal | `tests/browser/degraded_portal.spec.mjs` | 7 | Playwright |
| SSL lifecycle | `tests/api/test_ssl_*.py` (4 files) | 30 | pytest |
| Degraded mode | `tests/api/test_degraded_mode.py` | 11 | pytest |
| Deploy scripts | Makefile + `scripts/deploy-configwizzard.sh` | — | Bash/Make |
| Hardware lock | Makefile `lock`/`force-unlock` | — | Make |
| Full orchestration | Makefile `test-configwizzard-all` | — | Make |

## Missing Tests — Implementation Checklist

### 1. Admin SPA Playwright Tests (NEW FILE)

- [ ] `tests/browser/admin_spa.spec.mjs` — 8 tests:
  - [ ] Login page loads and accepts router password
  - [ ] Dashboard shows health status, uptime, version
  - [ ] Settings page renders schema-driven form
  - [ ] Settings page can change a value and save
  - [ ] Wallet page shows balance and mint info
  - [ ] Wifi page shows live radio status
  - [ ] Devices page lists connected clients
  - [ ] Layout sidebar navigation works

### 2. rpcd Plugin Security Tests (NEW FILE)

- [ ] `tests/api/test_rpcd_security.py` — 2 tests:
  - [ ] Shell injection in key/value params is blocked
  - [ ] ACL enforcement: unauthenticated write blocked

### 3. config save-identities Tests (NEW FILE)

- [ ] `tests/api/test_config_save_identities.py` — 2 tests:
  - [ ] Save identities round-trip: get → save → verify disk
  - [ ] Invalid identities JSON rejected

### 4. PR #11 ipk Lifecycle Tests (NEW FILE)

- [ ] `tests/api/test_ipk_lifecycle.py` — 3 tests:
  - [ ] Install ipk → verify files deployed
  - [ ] Verify rpcd plugin + ACL + uhttpd config after install
  - [ ] Uninstall ipk → verify cleanup

### 5. Admin/LuCI Port Tests (NEW FILE)

- [ ] `tests/api/test_admin_luci_ports.py` — 2 tests:
  - [ ] Admin SPA serves on port 80
  - [ ] LuCI serves on port 8080

### 6. Portal Pricing Integration Test

- [ ] Add to `tests/api/test_portal_pricing.py` — 1 test:
  - [ ] Portal HTML contains real pricing data from :2121 backend

## Execution Plan

### Phase 0: Environment Setup
- [ ] SSH connectivity check to alpha (10.47.41.1)
- [ ] SSH connectivity check to beta (192.168.244.1)
- [ ] Acquire hardware lock

### Phase 1: Deploy PR #124 to Both Routers
- [ ] Checkout PR #124 branch
- [ ] Cross-compile ARM64 binaries
- [ ] Deploy to alpha: stop → upload → start → verify
- [ ] Deploy to beta: stop → upload → start → verify

### Phase 2: Deploy PR #11 + configurationwizzard
- [ ] Build admin SPA from tollgate-captive-portal-site PR #11
- [ ] Build portal SPA from configurationwizzard captive-portal branch
- [ ] Deploy to alpha
- [ ] Deploy to beta

### Phase 3: Run All Existing Tests on Alpha
- [ ] `make test-configwizzard-all ROUTER=alpha`
- [ ] `TOLLGATE_SSH_HOST=10.47.41.1 pytest tests/api/ -v --expected-pr=124`

### Phase 4: Run All Existing Tests on Beta
- [ ] `make test-configwizzard-all ROUTER=beta`
- [ ] `TOLLGATE_SSH_HOST=192.168.244.1 pytest tests/api/ -v --expected-pr=124`

### Phase 5: Run New Tests on Alpha
- [ ] Admin SPA Playwright: `TOLLGATE_SSH_HOST=10.47.41.1 npx playwright test admin_spa.spec.mjs`
- [ ] New pytest: `TOLLGATE_SSH_HOST=10.47.41.1 pytest tests/api/test_rpcd_security.py tests/api/test_config_save_identities.py tests/api/test_admin_luci_ports.py tests/api/test_portal_pricing.py -v`

### Phase 6: Run New Tests on Beta
- [ ] Admin SPA Playwright: `TOLLGATE_SSH_HOST=192.168.244.1 npx playwright test admin_spa.spec.mjs`
- [ ] New pytest: `TOLLGATE_SSH_HOST=192.168.244.1 pytest tests/api/test_rpcd_security.py tests/api/test_config_save_identities.py tests/api/test_admin_luci_ports.py tests/api/test_portal_pricing.py -v`

### Phase 7: Cleanup
- [ ] Restore original configs on both routers
- [ ] Release hardware lock
- [ ] Generate summary report
