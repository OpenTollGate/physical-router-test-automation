# PR #120: Mint Resilience — Test Coverage & Fixes Plan

## Overview

Two workstreams targeting PR [#120](https://github.com/OpenTollGate/tollgate-module-basic-go/pull/120):

1. **Code fixes** in `tollgate-module-basic-go` (separate PR targeting `fix/degraded-mode-minimal`)
2. **Test coverage** in `physical-router-test-automation`

---

## Part A: Code Fixes

Branch: `fix/pr120-review-fixes` on `c03rad0r/test-stablechannel-tollgate-module-basic-go`
All 13 existing Go tests pass. Code compiles clean.

### Fix 1: Add `Shutdown()` to `MerchantInterface`

- [x] Add `Shutdown() error` to `MerchantInterface` in `src/merchant/merchant.go`
- [x] Add no-op `Shutdown() error` to `MerchantDegraded` in `src/merchant/merchant_degraded.go`
- [x] Add `Shutdown() error` to `stubMerchant` in `src/merchant/merchant_provider_test.go`

### Fix 2: Replace brittle error string matching

- [x] Add `"net"` and `"errors"` imports to `src/tollwallet/tollwallet.go`
- [x] Replace `strings.Contains` timeout check with `errors.As(err, &netErr)` + fallback string matching

### Fix 3: Resolve build-package.yml merge conflict

- [x] Merge `origin/main` and resolve trivial conflict (trailing comment line)
- [ ] **Blocked**: Push requires `workflow` scope on GitHub PAT — run `gh auth refresh -h github.com -s workflow` and retry

---

## Part B: Test Coverage

16 new tests across 4 files. All compile and collect successfully via pytest.

### Shared Helpers

- [x] Extract `is_full_merchant()`, `is_degraded()`, `wait_for_full_merchant()`, `wait_for_degraded()`, `skip_if_no_degraded_support()` into `lib/helpers.py`

### New Test Files

#### `tests/api/test_try_all_mints.py` — 3 tests

- [x] `test_first_mint_unreachable_second_works` — Config [unreachable, working], verify full merchant
- [x] `test_wallet_logs_show_mint_fallback` — Logs show fallback messages
- [x] `test_all_mints_unreachable_falls_to_degraded` — Config [unreachable, unreachable], verify degraded

#### `tests/api/test_merchant_provider.py` — 6 tests

- [x] `test_cli_balance_after_recovery` — CLI sees real balance post-recovery
- [x] `test_cli_wallet_info_after_recovery` — CLI sees real mint data post-recovery
- [x] `test_http_endpoints_degraded_responses` — All endpoints return structured degraded response
- [x] `test_http_endpoints_work_after_recovery` — All endpoints normal after recovery
- [x] `test_concurrent_requests_during_swap` — No 500s/panics during merchant swap
- [x] `test_cli_status_reflects_provider_state` — wallet_ok transitions correctly

#### `tests/api/test_recovery_lifecycle.py` — 3 tests

- [x] `test_multiple_recovery_cycles` — Degrade→recover→degrade→recover
- [x] `test_health_tracker_alive_after_recovery` — Tracker detects second degradation
- [x] `test_flapping_mint_hysteresis` — iptables block/unblock loop, no flip-flop

#### `tests/api/test_cli_degraded_operations.py` — 4 tests

- [x] `test_cli_balance_degraded` — Returns 0 with degraded status
- [x] `test_cli_info_degraded` — Returns degraded indicator
- [x] `test_cli_drain_degraded` — Returns error, no panic
- [x] `test_cli_fund_degraded` — Returns error, no panic

### Makefile Targets

- [x] `smoke-pr120` — Quick smoke (try-all-mints + CLI degraded, ~2 min)
- [x] `full-pr120` — Full PR #120 suite (~10 min)
- [x] `pr120-recovery` — Recovery lifecycle only

---

## Key Decisions

- **Try-all-mints test**: Use unreachable URL (`http://10.99.99.1:9999`) as first mint — simple, no external deps
- **Flapping mint test**: Use iptables block/unblock in a loop (simpler than FakeMintServer)
- **Recovery lifecycle tests**: Marked `extended` (not `destructive`) since teardown restores state
- **Code fixes**: Separate PR targeting `fix/degraded-mode-minimal` branch

## Next Steps

1. Run `gh auth refresh -h github.com -s workflow` to get workflow scope
2. Push `fix/pr120-review-fixes` to fork: `git push c03rad0r fix/pr120-review-fixes`
3. Create PR targeting `Amperstrand:fix/degraded-mode-minimal`
4. Run `make full-pr120` on hardware to validate all 16 tests
