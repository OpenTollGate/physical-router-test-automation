# Make-to-Pytest Migration Analysis

**Date**: 2026-05-20 (updated with live registry)
**Scope**: Which Makefile test targets have no pytest equivalent, and what's needed to migrate them.

## Live registry and runners

| Artifact | Purpose |
|----------|---------|
| [`config/make-pytest-map.yaml`](../config/make-pytest-map.yaml) | Source of truth: Make target → pytest node / runner |
| [`scripts/pymake.py`](../scripts/pymake.py) | CLI: `./scripts/pymake.py smoke-degraded --router alpha` |
| [`./pymake`](../pymake) | Shell wrapper (same as pymake.py) |
| [`make/migration.mk`](../make/migration.mk) | Root Makefile stubs forward to pymake |

Migrated root Makefile targets print a deprecation banner and invoke pymake (no dual-run of old shell tests).

```bash
make lock PHASE='smoke-degraded'
make smoke-degraded ROUTER=alpha          # → pymake → pytest
./scripts/pymake.py smoke-degraded --router alpha
make pytest-scenarios ROUTER=alpha        # all hardware scenarios
```

---

## Executive Summary

| Metric | Count |
|---|---|
| Total make test targets (leaf) | ~45 |
| Already covered by pytest | ~25 |
| **Makefile-only (uncovered)** | **~20** |
| New `router.py` methods needed | 6-8 |
| Estimated effort | 3-4 days |

The pytest framework already covers most functional areas through `tests/scenarios/`. The remaining gaps are: **real cert SSL** (needs Cloudflare DNS-01), **first-boot-offline** (needs serial console), **dynamic rebuild** (long-running state machine), and **captive portal / cashu payment** (needs Playwright or ADB, already covered by `.spec.mjs`).

---

## Coverage Matrix

### SSL Tests (PR #123)

| Make Target | What It Does | Pytest Equivalent | Status |
|---|---|---|---|
| `r-test-ssl-self-signed` | Apply self-signed cert, verify files/uci/listener | `tests/api/test_ssl_go_cli.py::test_ssl_self_signed_apply` | ✅ Covered |
| `r-test-ssl-self-signed-yes` | Apply with `--yes` flag | `test_ssl_go_cli.py::test_ssl_apply_with_yes_flag` | ✅ Covered |
| `r-test-ssl-remove` | Remove SSL, verify cleanup | `test_ssl_go_cli.py::test_ssl_remove_reverts_config` | ✅ Covered |
| `r-test-ssl-remove-no-backup` | Remove when no backup exists | `test_ssl_go_cli.py::test_ssl_remove_without_backup_errors` | ✅ Covered |
| `r-test-ssl-reapply` | Re-apply with existing backup | `test_ssl_go_cli.py::test_ssl_reapply_with_existing_backup` | ✅ Covered |
| `r-test-ssl-setup-verify` | Verify clean SSL state | `test_ssl_go_cli.py::test_ssl_status_not_configured` | ✅ Covered |
| `r-test-ssl-status` | Check `tollgate ssl status` output | `test_ssl_go_cli.py::test_ssl_status_shows_details` | ✅ Covered |
| `r-test-ssl-verify-cert` | Deep cert validation (SAN, CN, expiry) | `test_ssl_go_cli.py::test_ssl_cert_cn_san_valid` | ✅ Covered |
| `r-test-ssl-verify-nds` | Verify NDS allows port 443 | `test_ssl_go_cli.py::test_ssl_nds_allows_443` | ✅ Covered |
| `r-test-ssl-verify-no-dns` | No dnsmasq entry for self-signed | `test_ssl_go_cli.py::test_ssl_no_dnsmasq_for_self_signed` | ✅ Covered |
| `r-test-ssl-idempotent` | Apply twice, verify consistent | `test_ssl_go_cli.py::test_ssl_idempotent_apply` | ✅ Covered |
| `r-test-ssl-full` | Full lifecycle (apply→verify→remove→verify) | Covered by individual tests above | ✅ Covered |
| `r-test-ssl-comprehensive` | All self-signed tests in sequence (12 phases) | Covered by individual tests above | ✅ Covered |
| `r-test-ssl-real-cert` | Real cert via LE staging + Cloudflare DNS-01 | — | ❌ **Make-only** |
| `r-test-ssl-real-cert-remove` | Remove real cert (dnsmasq + NDS revert) | — | ❌ **Make-only** |
| `r-test-ssl-real-cert-full` | Full real cert lifecycle | — | ❌ **Make-only** |
| `r-test-ssl-all` | Comprehensive + real cert | — | ❌ **Make-only** (real cert part) |

**Real cert tests need**: Cloudflare DNS-01 credentials, a domain pointing to the router. This is infrastructure, not code — could be ported as `tests/api/test_ssl_real_cert.py` gated behind `TOLLGATE_CLOUDFLARE_TOKEN` env var.

### Hostname (PR #117)

| Make Target | What It Does | Pytest Equivalent | Status |
|---|---|---|---|
| `r-test-hostname` | Verify UCI, kernel, uhttpd hostname | `tests/api/test_hostname.py` (PR #117 gated) | ✅ Covered |

### Degraded Mode / Mint Health

| Make Target | What It Does | Pytest Equivalent | Status |
|---|---|---|---|
| `r-smoke-offline` | Block mint + restart + verify degraded | `test_mint_health.py::test_restart_into_degraded_mode` | ✅ Covered |
| `r-smoke-degraded` | Full degraded lifecycle (~3 min) | `test_mint_health.py::test_full_degraded_lifecycle` | ✅ Covered |
| `r-smoke-recovery` | Unblock + wait for recovery | `test_mint_health.py::test_recovery_to_full_merchant` | ✅ Covered |
| `r-smoke-degraded-recovery` | Degraded→recovery WITHOUT restart (BoltDB lock) | `test_boot_hygiene.py::test_degraded_recovery_no_restart` | ✅ Covered |
| `r-smoke-dynamic-rebuild` | Full→degraded→full rebuild (~10 min) | `test_boot_hygiene.py::test_dynamic_merchant_rebuild` | ✅ Covered |
| `r-smoke-degraded-connect` | Connect upstream while already degraded (RISKY) | — | ❌ **Make-only** |
| `r-test-offline-ops` | Verify wallet balance + status offline | `test_mint_health.py::test_offline_wallet_operations` | ✅ Covered |
| `r-test-first-boot-offline` | First boot with unreachable mint | `test_mint_health.py::test_first_boot_offline` | ✅ Covered |
| `r-test-no-mints` | No configured mints scenario | `test_mint_health.py::test_no_configured_mints` | ✅ Covered |
| `r-test-default-mints` | Verify default mint config | `test_mint_health.py` (partial) | ⚠️ Partial |
| `r-test-edge-cases` | Edge case testing (phase 8) | `test_mint_health.py` (partial) | ⚠️ Partial |
| `r-check-degraded` | Verify degraded merchant state | — | ❌ **Make-only** (helper) |
| `r-check-merchant` | Verify full merchant state | — | ❌ **Make-only** (helper) |

### Two-Router / Upstream

| Make Target | What It Does | Pytest Equivalent | Status |
|---|---|---|---|
| `r-smoke-upstream` | Two-router degraded upstream payment | `test_two_router.py::test_offline_renewal_via_lan` | ✅ Covered |
| `r-smoke-pin-upstream` | Upstream pin prevents scan-away | `test_two_router.py::test_pin_prevents_scan_away` | ✅ Covered |
| `r-smoke-degraded-upstream` | Connect online, degrade, verify offline renewal | `test_two_router.py::test_offline_renewal_via_lan` | ✅ Covered |
| `r-check-sta-health` | No stale/duplicate STA sections | `test_upstream_wifi.py::test_sta_health` | ✅ Covered |

### Startup Hygiene

| Make Target | What It Does | Pytest Equivalent | Status |
|---|---|---|---|
| `r-test-startup-hygiene` | Full power cycle: dead STA + remove ecash + reboot | `test_boot_hygiene.py::test_startup_hygiene_auto_switch` | ✅ Covered |
| `r-test-startup-hygiene-dead-only` | Boot with ONLY dead STA | `test_boot_hygiene.py::test_startup_hygiene_dead_only` | ✅ Covered |
| `r-test-startup-hygiene-setup` | Setup step (enable dead STA, remove ecash) | Inline in pytest test | ✅ Covered |
| `r-test-startup-hygiene-verify` | Verify auto-switch + restore | Inline in pytest test | ✅ Covered |

### Recovery

| Make Target | What It Does | Pytest Equivalent | Status |
|---|---|---|---|
| `r-test-reseller-guard` | Reseller mode guard | `test_reseller_mode.py::test_reseller_mode_toggle_persists` | ✅ Covered |
| `r-test-cleanup` | Cleanup after tests | Inline in pytest fixtures | ✅ Covered |

### Captive Portal / Payment (Playwright)

| Make Target | What It Does | Pytest Equivalent | Status |
|---|---|---|---|
| `r-test-captive-portal` | Playwright captive portal tests (.spec.mjs) | `tests/protocol/captive-portal.spec.mjs` | ⚠️ Same tests, different runner |
| `r-test-captive-portal-happy` | Happy-path only | `tests/protocol/captive-portal.spec.mjs` (subset) | ⚠️ Same tests, different runner |
| `r-test-cashu-payment` | E2E cashu payment via Playwright | `tests/protocol/payment-lifecycle.spec.mjs` | ⚠️ Similar, different runner |

These are JavaScript Playwright tests called from make. Not candidates for Python migration — they're already automated. Could be unified under pytest via `pytest-playwright` but marginal benefit.

---

## Make-Only Targets (Uncovered by Pytest)

### Priority 1: Worth Migrating

| Target | Effort | What's Needed |
|---|---|---|
| `r-test-ssl-real-cert` | 1 day | `router.py`: `ssl_apply_real(cert, key)`, Cloudflare env vars |
| `r-test-ssl-real-cert-remove` | 0.5 day | Same as above |
| `r-test-ssl-real-cert-full` | 0.5 day | Orchestrates the two above |
| `r-smoke-degraded-connect` | 0.5 day | `router.py`: `wait_for_upstream_connect()`, risk warning fixture |
| `r-test-default-mints` | 0.5 day | `router.py`: `get_mint_config()` |
| `r-test-edge-cases` | 0.5 day | Depends on what "edge cases" covers — needs investigation |

### Priority 2: Helpers / Low Value

| Target | Notes |
|---|---|
| `r-check-degraded` | State checker, could be a pytest fixture |
| `r-check-merchant` | State checker, could be a pytest fixture |
| `r-test-ssl-all` | Just orchestrates comprehensive + real cert |
| `r-test-cleanup` | Handled by pytest fixtures |

### Skip (Not Worth Migrating)

| Target | Reason |
|---|---|
| `r-test-captive-portal` / `r-test-captive-portal-happy` | Already Playwright `.spec.mjs`, not Python |
| `r-test-cashu-payment` | Already Playwright `.spec.mjs` |
| Serial console tests (`s-*`) | Different transport, niche use case |
| U-Boot recovery tests | Firmware upload, not worth it |

---

## `router.py` Methods Needed for Migration

Currently missing, all trivial wrappers over `self.ssh()`:

```python
# SSL (for real cert tests)
def ssl_apply_real(self, cert_path: str, key_path: str) -> dict
def ssl_status_parsed(self) -> dict          # Parse `tollgate ssl status` into dict

# UCI helpers (used by many tests)
def uci_get(self, path: str) -> str
def uci_set(self, path: str, value: str) -> None
def uci_commit(self, *configs: str) -> None

# DNS / hosts
def get_hosts_entries(self) -> list[str]
def add_hosts_block(self, hostname: str) -> None
def remove_hosts_block(self, hostname: str) -> None

# Upstream WiFi
def upstream_connect(self, ssid: str, password: str = None) -> None
def upstream_remove(self, ssid: str) -> None
def upstream_status(self) -> dict
```

Most of these are 3-5 lines each. The `_block_mint_via_hosts` helper already exists in `test_mint_health.py` and `test_boot_hygiene.py` as a local function — it should be promoted to `router.py`.

---

## Recommended Migration Order

1. **Promote `_block_mint_via_hosts` to `router.py`** as `block_mint()` / `unblock_mint()` (0.5 day)
2. **Add `uci_get`/`uci_set`/`uci_commit` to `router.py`** (0.5 day)
3. **Port real cert SSL tests** (`test_ssl_real_cert.py`) — biggest functional gap (1-2 days)
4. **Port `r-test-default-mints` and `r-test-edge-cases`** — small, quick wins (0.5 day)
5. **Port `r-smoke-degraded-connect`** — interesting scenario, needs care (0.5 day)

Total: ~3-4 days for complete pytest parity.

After migration, the Makefile targets remain as convenience aliases (`make smoke-degraded` still works) but the source of truth is pytest. Implementation status is tracked in `config/make-pytest-map.yaml` (`status: migrated`).

### Newly migrated (pytest)

- `r-smoke-degraded-connect` → `tests/scenarios/test_two_router.py::TestDegradedConnectWhileDegraded` (requires `TOLLGATE_ALLOW_RISKY=1`)
- `r-test-default-mints` → `test_default_mints_configured`
- `r-test-edge-cases` → `test_connect_to_unknown_ssid_fails` (upstream WiFi)
- `r-test-ssl-real-cert*` → `tests/api/test_ssl_real_cert.py` (requires Cloudflare token + domain)
- Serial ops → `lib/serial_console.py` + `pymake serial-recovery|serial-shell|serial-status`
