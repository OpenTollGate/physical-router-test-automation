# Cashu Ecosystem Research — TollGate Backend Decision Report

**Date**: July 22, 2026
**Author**: Sisyphus (AI Agent) for Amperstrand
**Scope**: Full ecosystem audit to determine Go vs Rust backend path

---

## Executive Summary

The Cashu ecosystem has converged on V4 tokens (CBOR) as the default format. Every major wallet and library — CDK, cashu-ts, Nutshell, eNuts, cashu.me — produces V4 tokens by default with proper short keyset ID resolution. **gonuts is the only implementation missing this resolution**, making it spec-non-compliant for V4 token consumption.

The NUT-00 spec explicitly requires: *"Wallets receiving a Token MUST support both short and full keyset ID representations. When a short keyset ID is encountered, the wallet MUST resolve it to the corresponding full keyset ID before processing."* gonuts violates this requirement.

---

## 1. NUT Specification Requirements

### NUT-00 V4 Token Format ([spec](https://github.com/cashubtc/nuts/blob/main/00.md))

V4 tokens use CBOR binary encoding (prefix `cashuB`). The keyset ID field (`i`) can be:
- **Full form**: 33-byte V2 keyset ID (66 hex chars)
- **Short form**: first 8 bytes of the full ID (16 hex chars)

**Critical requirement** (direct quote):
> "Wallets receiving a Token **MUST** support both short and full keyset ID representations. When a short keyset ID is encountered, the wallet **MUST** resolve it to the corresponding full keyset ID before processing the contained Proof objects."
>
> "The mint is unaware of the `s_id`. All API endpoints exposed by the mint use the full keyset ID."

### NUT-02 Keyset IDs ([spec](https://github.com/cashubtc/nuts/blob/main/02.md))

| Version | Prefix | Size | Status |
|---------|--------|------|--------|
| V1 | `00` | 8 bytes (16 hex) | Deprecated |
| V2 | `01` | 33 bytes (66 hex) | Current default (merged Jan 2026) |
| V3 | `02` | TBD | Under active review (BLS12-381, June 2026) |

---

## 2. Implementation Compatibility Matrix

| Implementation | Language | V4 Production | V4 Consumption | Short ID Resolution | Default Token | V2 Keysets | Maintenance |
|---|---|---|---|---|---|---|---|
| **CDK** | Rust | ✅ | ✅ | ✅ `from_short_keyset_id()` | V4 | ✅ Native | **Active** (OpenSats-funded) |
| **cashu-ts** | TypeScript | ✅ | ✅ | ✅ `keysetIds` param (v4.0+) | V4 | ✅ | **Active** |
| **Nutshell** | Python | ✅ | ✅ | ✅ | V4 (since 0.16.0) | ✅ | **Active** (reference impl) |
| **eNuts** | TypeScript | ✅ | ✅ | ✅ (uses cashu-ts) | V4 | ✅ | **Active** (206 stars) |
| **cashu.me** | TypeScript | ✅ | ✅ | ✅ (uses cashu-ts) | V4 | ✅ | **Active** (209 stars) |
| **gonuts** (elnosh) | Go | ❌ | Decode only | ❌ **MISSING** | V3 | ❌ | **Dead** (Aug 2025) |
| **gonuts-tollgate** | Go | ✅ | ✅ | ❌ **MISSING** | V3 | ⚠️ Partial | **Dead** (fork, unmaintained) |

**Key finding**: gonuts is the ONLY implementation in the entire Cashu ecosystem that lacks short keyset ID resolution. This was never discussed in dev calls because gonuts is outside the cashubtc org.

---

## 3. Dev Call History (V4 Timeline)

| Date | Event | Source |
|------|-------|--------|
| Feb 2024 | CBOR encoding discussed for token size reduction | [dev-call 2024-02-29](https://github.com/cashubtc/dev-calls/blob/main/minutes/2024-02-29.md) |
| Apr 2024 | V4 binary format spec proposed (NUT-00 PR #109) | [dev-call 2024-04-24](https://github.com/cashubtc/dev-calls/blob/main/minutes/2024-04-24.md) |
| Jun 2024 | CDK implements V4 (PR #158) with short ID resolution | CDK repo |
| Aug 2024 | cashu-ts implements V4 read/write; testing on staging.cashu.me | [dev-call 2024-08-29](https://github.com/cashubtc/dev-calls/blob/main/minutes/2024-08-29.md) |
| Oct 2024 | V2 keyset IDs proposed (NUT-02 PR #182) | [dev-call 2024-10-31](https://github.com/cashubtc/dev-calls/blob/main/minutes/2024-10-31.md) |
| Dec 2024 | Binary token serialization merged into spec (PR #199) | [dev-call 2024-12-12](https://github.com/cashubtc/dev-calls/blob/main/minutes/2024-12-12.md) |
| Jan 2026 | V2 keyset IDs merged into spec | [dev-call 2026-01-29](https://github.com/cashubtc/dev-calls/blob/main/minutes/2026-01-29.md) |
| Feb 2026 | V2 keysets become default in CDK | [dev-call 2026-02-26](https://github.com/cashubtc/dev-calls/blob/main/minutes/2026-02-26.md) |
| Jun 2026 | V3 keysets (BLS12-381) under active review | [dev-call 2026-06-25](https://github.com/cashubtc/dev-calls/blob/main/minutes/2026-06-25.md) |

**No mention of gonuts or short keyset ID resolution issues in ANY dev call (2023-2026).**

---

## 4. Experimental Evidence (This Session)

### A/B Test Results (localhost virtual lab, July 2026)

| Test | Token Format | Keyset Version | Keyset ID | Result | Evidence |
|------|-------------|----------------|-----------|--------|----------|
| **V3 + V1** | `cashuA` JSON | V1 (`00` prefix) | `008e808b89acc141` | ✅ `kind=1022, allotment=66060288` | Full end-to-end success |
| **V3 + V2** | `cashuA` JSON | V2 (`01` prefix) | `01df97b6fb8a...` (66 hex) | ✅ `kind=1022, allotment=88080384` | Full end-to-end success |
| **V4 + V1** | `cashuB` CBOR | V1 (`00` prefix) | `008e808b89acc141` (8 bytes) | ✅ Payment PROCESSES | `Amount after swap: 3` in logs. V1 short ID = full V1 ID. |
| **V4 + V2** | `cashuB` CBOR | V2 (`01` prefix) | `01df97b6fb8a572a` (8 bytes truncated) | ❌ Fails at swap | `NUT02: ID length invalid`. gonuts doesn't resolve short ID. |

### Root Cause (Source Code Verified)

**gonuts code path** (`gonuts-tollgate@v0.7.1`):
```
TokenV4.Proofs()
  → keysetId = hex.EncodeToString(tokenV4Proof.Id)  // "01df97b6fb8a572a"
  → proof.Id = keysetId                               // Set directly, NO RESOLUTION
  → swap request sent to mint with 8-byte ID
  → mint rejects: "NUT02: ID length invalid"
```

**CDK code path** (correct):
```
TokenV4.proofs(mint_keysets)
  → long_id = Id::from_short_keyset_id(&short_id, mint_keysets)?
  → proof.id = long_id                               // Full 33-byte ID
  → swap request sent to mint with full ID
  → mint accepts ✅
```

---

## 5. Ecosystem Issue Audit

### Key Issues Found

| Repo | Issue | State | Description |
|------|-------|-------|-------------|
| **nutshell #961** | CLOSED | V4 tokens from testnut.cashu.space don't work in some wallets. Error: "Couldn't map short keyset ID `01884a74bb2fc5ee`". Closed as wallet bug, not mint bug. |
| **cdk #2009** | CLOSED | Include inactive keysets in wallet token operations. Fixed resolution for rotated keysets. |
| **cdk #1492** | MERGED | Length check for short keyset IDs. |
| **cdk #2096** | MERGED | Simplify keyset management to three core functions. |
| **cashu-ts #177** | CLOSED | Can't convert V3 to V4 with non-hex keyset IDs. Not a short ID issue. |

### Gonuts-Specific Issues
- **Zero mentions** of gonuts across all cashubtc repos
- gonuts is NOT part of the cashubtc organization
- No community awareness of gonuts's V4 short keyset ID gap

---

## 6. Recommendation

### Best Option: Migrate to Rust/CDK

| Factor | Fix gonuts | Migrate to Rust/CDK |
|--------|-----------|---------------------|
| Effort | Medium (implement resolution) | Medium (2 feature gaps: LuCI, sessions) |
| V4 support | After fix | ✅ Native, free |
| V2 keyset payments | ✅ Already works (V3 format) | ✅ Native |
| Future NUT changes | Manual each time | ✅ Automatic via CDK |
| Ecosystem alignment | Against the grain | ✅ With ecosystem |
| Community support | None (gonuts dead) | ✅ CDK + OpenSats funded |
| Maintenance burden | High (fork tracking) | Low (upstream handles it) |
| V3 keysets (BLS12-381) | Would need full rewrite | ✅ Will get free via CDK |

### Why Not Fix gonuts?

1. gonuts upstream is **dead** (last commit Aug 2025, no activity)
2. Adding short keyset ID resolution is ~20-50 lines of Go, but:
   - Need to fetch keyset list from mint before each swap
   - Need to match 8-byte prefix against known V2 keysets
   - Need to handle ambiguity (multiple matches)
   - Need to maintain this code forever as spec evolves
3. V3 keysets (BLS12-381) are coming — gonuts would need a **complete cryptographic rewrite**
4. No Go CDK binding exists or is planned

### Migration Effort (Verified)

From `docs/go-to-rust-migration-plan.md`:
- **Phase 0**: ✅ COMPLETE (payments verified on localhost)
- **Phase 1**: 1-2 weeks (session persistence + CLI socket)
- **Phase 2-3**: 3-4 weeks (staging + canary)
- **Total**: 4-6 weeks (profit share already done)
- Both backends use same `.ipk` name, config path, service name — swap is one deploy command

### Short-Term Bridge (If Needed)

If you need V4 support before completing migration:
1. **Use V3 tokens only** — all wallets support V3 (`cashuA` prefix). V3 stores full keyset IDs as strings. Works for all keyset versions.
2. **Add `--legacy` flag** — Nutshell and CDK both support `--legacy` / `-v` flag to produce V3 instead of V4
3. **No user action needed** — users can paste V3 tokens, they work everywhere

---

## 7. Action Items

| Priority | Action | Effort | Dependency |
|----------|--------|--------|------------|
| **P0** | Accept V4 gap as known limitation, document workaround (use V3) | Done | — |
| **P1** | Start Rust backend Phase 1 (session persistence) | 1-2 weeks | Architecture decision |
| **P2** | File issue in cashubtc/nuts asking for NUT-00 compliance test suite | 1 hour | — |
| **P3** | Consider filing gonuts V4 gap as a finding in elnosh/gonuts (for posterity) | 30 min | — |
