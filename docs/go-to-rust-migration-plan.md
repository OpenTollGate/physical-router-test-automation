# Go → Rust Backend Migration Plan

**Version:** 2.0 | **Date:** 2026-07-21 | **Status:** Draft

---

## Executive Summary

The TollGate project maintains two backends. Go v1 (production, v0.5.0) and Rust v1 (development, no releases). Both share the same package name, config format, init script, and API surface, making switching friction-free.

**Migration is justified by two hard blockers in the Go backend:**

1. **V2 keyset payment failure** — Go backend *starts* with V2 mints configured (no crash), but POST / returns `400: "invalid V3 token"` when processing tokens signed with V2 keysets. The gonuts wallet library cannot verify V2 signatures. Modern CDK and Nutshell mints use V2 keysets natively.
2. **V4 token rejection** — Modern Cashu wallets (eNuts, cashu.me) produce V4 (CBOR) tokens. Gonuts only handles V1/V3. Go backend rejects V4 with "invalid V3 token".

Rust backend with CDK handles both without issue. The migration path is straightforward: same `.ipk`, same config, same ports. Main work is closing feature gaps (LuCI UI, session persistence, profit share).

---

## 1. Go Backend: Current State

| Property | Value |
|----------|-------|
| Repo | `OpenTollGate/tollgate-module-basic-go` (`main`) |
| Release | v0.5.0 (2026-07-03) |
| Wallet library | `Origami74/gonuts-tollgate` v0.6.1 |
| Ports | 2121 (API), 2050 (portal), 8080 (LuCI) |

### Wallet library

Origami74/gonuts-tollgate v0.6.1 is an active fork of elnosh/gonuts (dead since Aug 2025). It handles V1 keyset IDs and V1/V3 tokens. It cannot verify V2 keyset signatures or decode V4 CBOR tokens.

### Feature matrix

| Feature | Status | Notes |
|---------|--------|-------|
| API (7 endpoints) | Done | GET/POST /, usage, balance, whoami, ln-invoice |
| LuCI Admin UI | Done | 5 tabs, port 8080 |
| CLI Socket | Done | `/var/run/tollgate.sock` |
| Session persistence | Done | `sessions.json`, survives restart |
| V1 keysets + V1/V3 tokens | Done | Works with testnut.cashu.exchange |
| **V2 keysets** | **Partial** | Backend starts fine, but token payments fail with 400: "invalid V3 token" |
| **V4 tokens** | **Rejected** | Returns "invalid V3 token" |
| Degraded mode | Done | MintHealthTracker |
| Captive portal | Done | Nodogsplash on port 2050 |
| Profit share | Done | Upstream merchant LN autopay |

### Known limitations

| Limitation | Severity | Impact |
|------------|----------|--------|
| V2 keyset payments broken | High | Cannot accept tokens from CDK/Nutshell mints |
| V4 tokens rejected | High | Modern wallets cannot pay |
| Fork maintenance | Medium | Must track gonuts-tollgate separately |

---

## 2. Rust Backend: Current State

| Property | Value |
|----------|-------|
| Repo | `Amperstrand/tollgate-rs-ai-research-and-experiments` (`experimental`) |
| Release | None (dev only) |
| Wallet library | `cashubtc/cdk` v0.17.3 (official Cashu dev kit) |
| Ports | 2121 (API), 2050 (portal) |

### Feature gaps vs Go

| Feature | Status | Notes |
|---------|--------|-------|
| API (7 endpoints) | Done | Full parity |
| V1/V2 keysets + V1/V3/V4 tokens | Done | CDK native support |
| Degraded mode | Done | |
| Captive portal | Done | |
| LuCI UI | **Missing** | High priority gap |
| CLI Socket | **Experimental** | Partial implementation |
| Session persistence | **Missing** | In-memory only; sessions lost on restart |
| Profit share | **Missing** | Low priority gap |

---

## 3. Feature Comparison

| Feature | Go | Rust | Gap |
|---------|----|------|-----|
| V1 keysets | Done | Done | None |
| **V2 keysets** | **Partial (starts but payments fail)** | **Done** | **Rust advantage** |
| V1/V3 tokens | Done | Done | None |
| **V4 tokens** | **Rejected** | **Done** | **Rust advantage** |
| LuCI UI | Done | Missing | Rust gap (high) |
| CLI socket | Done | Experimental | Rust gap (medium) |
| Session persistence | Done | Missing | Rust gap (medium) |
| Profit share | Done | Missing | Rust gap (low) |
| Degraded mode | Done | Done | None |
| Captive portal | Done | Done | None |

**Summary:** Rust wins on wallet protocol support (V2 payments, V4 tokens). Go wins on operational tooling (LuCI, persistence, profit share). The protocol gaps are user-facing blockers. The operational gaps are internal tooling that can be rebuilt.

---

## 4. Compatibility Layer (accurate)

Both backends share identical packaging:

| Aspect | Both |
|--------|------|
| Package name | `tollgate-wrt.ipk` |
| Config path | `/etc/tollgate/config.json` |
| Service name | `tollgate-wrt` |
| Binary path | `/usr/bin/tollgate-wrt` |
| Init script | `/etc/init.d/tollgate-wrt` |
| Ports | 2121, 2050 |

Switching is a single deploy:

```bash
TOLLGATE_BACKEND=rust ./scripts/deploy-ci.sh experimental '' 192.168.x.x
```

Rust ignores unknown config fields (like `profit_share`, `ln_address`) without crashing. Config format is compatible.

### Config format difference

| Config field | Go | Rust |
|--------------|----|------|
| V2 keyset mints | Starts but payments fail (400) | Fully supported |
| `profit_share` | Used | Ignored |
| `ln_address` | Used | Ignored |

---

## 5. Migration Path

### Phase 0: Validation (week 1)

Verify config compatibility. Test all 7 API endpoints. Benchmark performance. Test V4 token acceptance and V2 keyset payments on Rust.

### Phase 1: Core parity (weeks 2-4)

Complete CLI socket. Implement session persistence (`sessions.json`). Build session import from Go format. Extend test coverage.

### Phase 2: Staging (weeks 5-6)

Deploy to staging routers. Full test suite. Monitor regressions. Document CLI workflow (no LuCI). Test rollback procedure.

### Phase 3: Canary (weeks 7-8)

10% of production routers. Monitor error rates, latency, session duration. Collect operator feedback.

### Phase 4: Full rollout (weeks 9-10)

Deploy to all routers. Update docs. Keep Go as fallback.

### Phase 5: LuCI (weeks 11-13, optional)

Port or rebuild admin UI.

**Minimal viable migration:** Phases 0-1, roughly 3-4 weeks. Trade-off: no LuCI, operators use CLI.

### Effort estimate

| Phase | Effort |
|-------|--------|
| Validation | 1 week |
| Core parity | 2-3 weeks |
| Staging + canary | 2 weeks |
| Full rollout | 1-2 weeks |
| LuCI (optional) | 3-4 weeks |
| **Total (no LuCI)** | **6-8 weeks** |
| **Total (with LuCI)** | **10-13 weeks** |

---

## 6. Risks

### Technical

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Session loss on migration | High | High | Schedule during low-traffic; implement import |
| Wallet reset | High | Medium | Document re-funding for merchants |
| NDS integration regression | Low | High | Full captive portal test suite |
| CDK API breaks | Medium | Medium | Pin version |
| Performance regression | Low | Medium | Benchmark both backends |

### Go-backend-specific (migration drivers)

| Risk | Probability | Impact |
|------|-------------|--------|
| V2 keyset payments broken (400) | Certain | High: CDK/Nutshell mints unusable |
| V4 tokens rejected | Certain | High: modern wallets can't pay |
| Fork maintenance burden | Medium | Low: actively maintained for now |

---

## 7. Recommendations

**Migrate to Rust as the primary backend.**

### Why now, not later

1. **V2 payments are broken in Go.** Not a crash, but a silent failure. The backend starts with V2 mints configured, token minting works, but actual payment processing returns 400. This was investigated June 2026 (issue #176, fixed by PR #167 for startup, but not for token verification). Gonuts cannot verify V2 signatures. This will not be fixed without replacing the wallet library, which means either forking gonuts heavily or switching to Rust/CDK.

2. **V4 tokens are rejected.** As Cashu wallets default to V4 (CBOR), Go becomes unusable for an increasing share of users.

3. **CDK is the official Cashu library.** No fork maintenance. Community-maintained, regularly audited, supports the full evolving protocol.

4. **The cost is low.** Both backends share the same packaging. Switching is a deploy command, not a migration project. The actual work is closing the feature gaps (LuCI, persistence, CLI socket).

### Rollback procedure

1. Deploy Go: `TOLLGATE_BACKEND=go ./scripts/deploy-ci.sh main`
2. If config contains V2 mints, payments will fail silently (not crash). Remove V2 mints from config if full Go compatibility is needed, or accept V2 payment failures if V1 mints remain configured.
3. Sessions and wallet state are lost (reset on switch). Schedule accordingly.

---

## Appendix: References

| Component | Repo | Branch |
|-----------|------|--------|
| Go backend | `OpenTollGate/tollgate-module-basic-go` | `main` (v0.5.0) |
| Go wallet | `Origami74/gonuts-tollgate` | v0.6.1 |
| Go wallet (dead upstream) | `elnosh/gonuts` | dead since Aug 2025 |
| Rust backend | `Amperstrand/tollgate-rs-ai-research-and-experiments` | `experimental` |
| Rust wallet | `cashubtc/cdk` | v0.17.3 |
| Test framework | `OpenTollGate/physical-router-test-automation` | `main` |

### Glossary

- **CDK**: Cashu Development Kit, official Rust wallet library
- **gonuts-tollgate**: Origami74's fork of gonuts (Go wallet library)
- **V1/V3/V4 tokens**: Cashu token formats (V1=legacy, V3=current JSON, V4=CBOR)
- **V1/V2 keyset IDs**: Keyset identifier versions (V1=`00` prefix, 16 hex; V2=`01` prefix, 66 hex)
